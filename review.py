"""Review gate. These actions ONLY RECORD the Growth Lead's decision (approved / edited / rejected) in Neon. There is no send function.
An edit also replaces the existing Gmail draft so Gmail and the dashboard always show the same text. She sends from Gmail herself."""
import difflib
import db, gmail_drafts, research

# status -> (plain-words label, tone). Tones map to colours in static/theme.css: success / warning / danger / neutral.
STATUS_VIEW = {
    "QUEUED": ("Waiting to start", "neutral"), "WORKING": ("Working", "neutral"),
    "DRAFT_READY": ("Draft ready for your review", "warning"), "QUALIFIED": ("Qualified, contact step next", "neutral"),
    "DISQUALIFIED": ("Not a fit (dropped)", "neutral"), "LOW_PRIORITY": ("Low priority", "neutral"),
    "INSUFFICIENT_EVIDENCE": ("Not enough evidence", "warning"), "NO_SUITABLE_CONTACT": ("No suitable contact found", "warning"),
    "CONTACT_LOOKUP_PAUSED": ("Contact lookup paused", "warning"), "INSUFFICIENT_PAIN_EVIDENCE": ("Not enough to write an honest email", "warning"),
    "EMAIL_QA_FAILED": ("Draft failed quality checks", "danger"), "ERROR": ("Error on this company", "danger"),
}
STAGE_LABEL = {"reading_website": "Reading the website…", "finding_contact": "Finding the right person…", "writing_email": "Writing and checking the email…"}

def view(row, decision=None):
    """Plain-words status for a company row (+ the review decision on its current email, if any)."""
    import datetime as dt
    if decision == "rejected":
        return {"label": "Rejected by you", "tone": "danger", "key": "REJECTED"}
    if decision == "approved":
        return {"label": "Approved by you", "tone": "success", "key": "APPROVED"}
    if decision == "edited":
        return {"label": "Edited by you, not yet approved", "tone": "warning", "key": "EDITED"}
    age = 0
    if row.get("updated_at"):
        age = (dt.datetime.now(dt.timezone.utc) - row["updated_at"]).total_seconds()
    if row["status"] == "QUEUED":
        return {"label": "Never started: run it again" if age > 1800 else "Waiting to start", "tone": "warning" if age > 1800 else "neutral", "key": "QUEUED"}
    if row.get("stage") not in (None, "done") and row["status"] in ("WORKING", "QUALIFIED"):
        if age > 600:                                  # nothing saved for 10 minutes: the run was cut off
            return {"label": "Run was interrupted: run it again", "tone": "danger", "key": "STALE"}
        return {"label": STAGE_LABEL.get(row.get("stage"), "Working"), "tone": "neutral", "key": "WORKING"}
    label, tone = STATUS_VIEW.get(row["status"], (row["status"], "neutral"))
    return {"label": label, "tone": tone, "key": row["status"]}

def _current(cid):
    return db.one("SELECT * FROM emails WHERE company_id=%s AND is_current", (cid,))

def mark_opened(cid):
    """Starts the review clock: the most recent time the Growth Lead opened this draft."""
    db.x("UPDATE emails SET opened_at=now() WHERE company_id=%s AND is_current AND review_decision='pending'", (cid,))

def _record(cid, decision, note=None):
    e = _current(cid)
    if not e:
        raise ValueError("This company has no draft to review.")
    db.x("UPDATE emails SET review_decision=%s, review_note=%s, reviewed_at=now(), "
         "review_seconds=CASE WHEN opened_at IS NOT NULL THEN EXTRACT(EPOCH FROM (now()-opened_at)) END WHERE id=%s", (decision, note, e["id"]))
    co = db.one("SELECT run_id FROM companies WHERE id=%s", (cid,))
    research.log(co["run_id"], cid, "review_" + decision, {"email_id": e["id"], "note": note})
    return e

def approve(cid):
    _record(cid, "approved")

def reject(cid, note=""):
    _record(cid, "rejected", (note or "")[:300])

def edit(cid, subject, body):
    """Saves her edit and REPLACES the existing Gmail draft (same draft id). Returns an error text if Gmail could not be updated."""
    e = _current(cid)
    if not e:
        raise ValueError("This company has no draft to edit.")
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise ValueError("Subject and email cannot be empty.")
    db.x("UPDATE emails SET subject=%s, body=%s, words=%s WHERE id=%s", (subject, body, len(body.split()), e["id"]))
    _record(cid, "edited")
    try:
        draft_id, verb = gmail_drafts.create_or_replace(subject, body, e["gmail_draft_id"])
        db.x("UPDATE emails SET gmail_draft_id=%s, gmail_draft_error=NULL, draft_updated_at=now(), "
             "draft_created_at=COALESCE(draft_created_at, now()) WHERE id=%s", (draft_id, e["id"]))
        return None
    except (gmail_drafts.GmailSetupError, gmail_drafts.GmailError) as err:
        db.x("UPDATE emails SET gmail_draft_error=%s WHERE id=%s", (str(err)[:400], e["id"]))
        return str(err)[:400]

def contact_feedback(cid, correct):
    """The Growth Lead's verdict on the chosen contact (feeds the contact-accuracy metric)."""
    n = db.x("UPDATE contacts SET contact_correct=%s WHERE company_id=%s AND chosen", (bool(correct), cid))
    if not n:
        raise ValueError("This company has no chosen contact.")

def similarity(a, b):
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()
