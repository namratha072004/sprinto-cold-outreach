"""Review gate: the Growth Lead approves, edits or rejects. Nothing is sent without approve().
Used by app.py and by the terminal path in pipeline.py. All state lives in output/<domain>/state.json."""
import difflib, datetime as dt
import settings as S
import mailer

# status -> (plain-words label, tone). Tones map to colours in static/theme.css (success/warning/danger/neutral).
STATUS_VIEW = {
    "QUEUED": ("Waiting to start", "neutral"),
    "STARTED": ("Working", "neutral"),
    "DRAFT_READY": ("Draft ready for your review", "warning"),
    "QUALIFIED": ("Qualified", "neutral"),
    "DISQUALIFIED": ("Not a fit (dropped)", "neutral"),
    "LOW_PRIORITY": ("Low priority", "neutral"),
    "INSUFFICIENT_EVIDENCE": ("Not enough evidence", "warning"),
    "NO_SUITABLE_CONTACT": ("No suitable contact found", "warning"),
    "CONTACT_LOOKUP_PAUSED": ("Contact lookup paused", "warning"),
    "INSUFFICIENT_PAIN_EVIDENCE": ("Not enough to write an honest email", "warning"),
    "EMAIL_QA_FAILED": ("Draft failed quality checks", "danger"),
    "ERROR": ("Error on this company", "danger"),
}
STAGE_LABEL = {"reading_website": "Reading the website…", "finding_contact": "Finding the right person…",
               "writing_email": "Writing and checking the email…"}

def view_status(st):
    rv = st.get("review") or {}
    if rv.get("decision") == "rejected":
        return {"label": "Rejected by you", "tone": "danger", "key": "REJECTED"}
    if rv.get("decision") == "approved":
        if rv.get("send_status") == "SENT":
            return {"label": "Approved and sent to the demo address", "tone": "success", "key": "SENT"}
        return {"label": "Approved, not sent yet", "tone": "warning", "key": "APPROVED_NOT_SENT"}
    if st.get("status") == "QUEUED":
        return {"label": "Waiting to start", "tone": "neutral", "key": "QUEUED"}
    if st.get("status") == "STARTED" or st.get("stage") not in (None, "done"):
        return {"label": STAGE_LABEL.get(st.get("stage"), "Working"), "tone": "neutral", "key": "WORKING"}
    label, tone = STATUS_VIEW.get(st.get("status"), (st.get("status", "Unknown"), "neutral"))
    return {"label": label, "tone": tone, "key": st.get("status")}

def _similarity(a, b):
    return round(difflib.SequenceMatcher(None, a or "", b or "").ratio(), 3)

def mark_opened(domain):
    """Start the review clock (most recent time the Growth Lead opened this draft)."""
    def f(st):
        rv = st.setdefault("review", {})
        if st.get("status") == "DRAFT_READY" and not rv.get("decision"):
            rv["opened_at"] = S.now_iso()
    S.update_state(domain, f)

def save_edit(domain, subject, body):
    def f(st):
        if st.get("status") != "DRAFT_READY" or (st.get("review") or {}).get("decision"):
            raise ValueError("This company has no open draft to edit.")
        st["draft"] = {"subject": subject.strip(), "body": body.strip()}
    return S.update_state(domain, f)

def _decide_common(st, subject, body):
    rv = st.setdefault("review", {})
    orig = st.get("original_draft") or st.get("draft") or {}
    rv["final_subject"], rv["final_body"] = subject.strip(), body.strip()
    rv["edited"] = (subject.strip() != orig.get("subject", "").strip()) or (body.strip() != orig.get("body", "").strip())
    rv["edit_similarity"] = _similarity(orig.get("subject", "") + "\n" + orig.get("body", ""), subject.strip() + "\n" + body.strip())
    rv["decided_at"] = S.now_iso()
    if rv.get("opened_at"):
        rv["review_seconds"] = round((dt.datetime.fromisoformat(rv["decided_at"]) - dt.datetime.fromisoformat(rv["opened_at"])).total_seconds(), 1)
    return rv

def reject(domain, subject, body, reason=""):
    def f(st):
        if st.get("status") != "DRAFT_READY" or (st.get("review") or {}).get("decision"):
            raise ValueError("This draft was already decided.")
        rv = _decide_common(st, subject, body)
        rv["decision"], rv["reject_reason"] = "rejected", reason[:300]
    return S.update_state(domain, f)

def compose_email_text(st, body):
    """The email as it lands in the demo inbox: the approved text, then the decision trail underneath."""
    a, c = st["assessment"], st["contact"]
    lines = [body, "", "----- DEMO DECISION TRAIL (not part of the email) -----",
             f"WHY THIS COMPANY QUALIFIED: {st['reason']}"]
    for name, g in st["gates"].items():
        lines.append(f"  gate {name}: {g['state']} - {g['detail']}")
    for k, d in a["dimensions"].items():
        ex = d["evidence"][0] if d["evidence"] else None
        lines.append(f"  {k}: level {d['level']} ({d['state']})" + (f" | \"{ex['excerpt']}\" ({ex['source_url']})" if ex else " | no evidence"))
    lines.append(f"  funding stage: {a['funding']['stage']} | flags: {', '.join(st.get('flags') or []) or 'none'}")
    lines.append(f"WHY THIS PERSON: {c['why']}")
    lines.append(f"INTENDED RECIPIENT (research data, NOT emailed): {c['email']} | Hunter verification: {c.get('verification')}")
    lines.append(f"HOOK SOURCE: {st['pain_points'][0]['evidence']['source_url']}")
    return "\n".join(lines)

def _send(domain):
    """Send the approved email to the demo address only. Guards against double sends."""
    claimed = {}
    def claim(st):
        rv = st["review"]
        if rv.get("send_status") in ("SENT", "SENDING"):
            claimed["skip"] = rv["send_status"]
            return
        rv["send_status"] = "SENDING"
    st = S.update_state(domain, claim)
    if claimed.get("skip"):
        return st
    subject = "[DEMO] " + st["review"]["final_subject"]
    text = compose_email_text(st, st["review"]["final_body"])
    status, err = "SENT", None
    try:
        mailer.send_demo(subject, text)
    except (mailer.MailerNotConfigured, mailer.MailerError) as e:
        status, err = "NOT_SENT", str(e)
    def done(s):
        s["review"]["send_status"] = status
        s["review"]["send_error"] = err
        if status == "SENT":
            s["review"]["sent_at"] = S.now_iso()
    return S.update_state(domain, done)

def approve(domain, subject, body, via="web app"):
    def f(st):
        if st.get("status") != "DRAFT_READY" or (st.get("review") or {}).get("decision"):
            raise ValueError("This draft was already decided.")
        rv = _decide_common(st, subject, body)
        rv["decision"], rv["via"] = "approved", via
    S.update_state(domain, f)
    return _send(domain)

def retry_send(domain):
    st = S.read_state(domain)
    if not st or (st.get("review") or {}).get("decision") != "approved":
        raise ValueError("Only an approved email can be sent.")
    if st["review"].get("send_status") == "NOT_SENT":
        S.update_state(domain, lambda s: s["review"].update(send_status=None))
    return _send(domain)

def contact_feedback(domain, correct):
    """Growth Lead's verdict on whether the contact was the right person (feeds the contact-accuracy metric)."""
    def f(st):
        st.setdefault("review", {})["contact_correct"] = bool(correct)
    return S.update_state(domain, f)
