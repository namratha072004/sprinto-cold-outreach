"""Steps 7-9 for one QUALIFIED company, all saved in Neon: Hunter contact -> two pain hypotheses -> email + QA -> Gmail draft.
Plain code decides and checks; the AI only scores, reads and writes. The software never sends: the draft goes to Gmail Drafts.
Usage:  .venv/bin/python run_one.py cal.com"""
import time, datetime as dt
import requests
import db, research, gmail_drafts
import rubric as R
import settings as S
import pipeline as P

# ---------------------------------------------------------------- Hunter (only contact provider), capped and cached in Neon
def _pause(run_id, why):
    db.x("UPDATE runs SET hunter_paused=%s WHERE id=%s", (why, run_id))
    raise P.HunterPaused(why, run_wide=True)

def hunter_lookup(run_id, domain):
    cached = db.one("SELECT * FROM hunter_cache WHERE domain=%s", (domain,))
    if cached:                                                # every result is cached, including empty ones
        return {"data": cached["data"], "from_cache": True, "credits_used": cached["credits_used"]}
    run = db.one("SELECT hunter_calls, hunter_paused FROM runs WHERE id=%s", (run_id,))
    if run["hunter_paused"]:
        raise P.HunterPaused(run["hunter_paused"], run_wide=True)
    cap = research.CFG["limits"]["hunter_calls_per_run"]
    if run["hunter_calls"] >= cap:
        _pause(run_id, f"the limit of {cap} Hunter lookups per run was reached")
    key = S.env("HUNTER_API_KEY")
    if not key:
        raise P.HunterPaused("HUNTER_API_KEY is missing", run_wide=True)
    left = P.hunter_remaining()                               # free call
    if left is not None and left < 1:
        _pause(run_id, "the Hunter quota is used up")
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search", headers={"X-API-KEY": key}, timeout=25,
                         params={"domain": domain, "limit": research.CFG["limits"]["hunter_candidates_per_lookup"], "type": "personal"})
    except requests.RequestException as e:
        raise P.HunterPaused(f"could not reach Hunter ({type(e).__name__})")
    try:
        used = float(r.headers.get("X-Credits-Used", 0) or 0)
    except ValueError:
        used = 0.0
    db.x("UPDATE runs SET hunter_calls=hunter_calls+1, hunter_credits=hunter_credits+%s WHERE id=%s", (used, run_id))
    # Hunter docs: 401 bad key, 403 = RATE limit, 429 = USAGE (quota) limit
    if r.status_code == 401:
        _pause(run_id, "Hunter rejected the API key (401)")
    if r.status_code == 403:
        _pause(run_id, "Hunter says rate limit reached (403); wait a minute and retry")
    if r.status_code == 429:
        _pause(run_id, "the Hunter usage quota is used up (429)")
    if r.status_code != 200:
        raise P.HunterPaused(f"Hunter returned HTTP {r.status_code}")
    data = r.json().get("data", {})
    db.x("INSERT INTO hunter_cache (domain, data, credits_used) VALUES (%s,%s,%s) ON CONFLICT (domain) DO NOTHING", (domain, db.J(data), used))
    return {"data": data, "from_cache": False, "credits_used": used}

# ---------------------------------------------------------------- one company
def _merge_usage(old, new):
    out = dict(old or {})
    for k, v in (new or {}).items():
        out[k] = out.get(k, 0) + v if isinstance(v, (int, float)) and isinstance(out.get(k, 0), (int, float)) else v
    return out

def finish_company(run_id, domain):
    cfg = research.ensure_rubric()
    co = db.one("SELECT * FROM companies WHERE run_id=%s AND domain=%s", (run_id, domain))
    if not co or co["status"] not in ("QUALIFIED", "CONTACT_LOOKUP_PAUSED"):
        raise ValueError(f"{domain} is not waiting for a contact lookup.")
    cid, a, sources = co["id"], co["assessment"], co["sources_text"] or {}
    t0, usage, st = time.time(), {}, {}
    # the one Gmail draft for this company, even if it came from an earlier run (never create a second)
    prev = db.one("SELECT e.gmail_draft_id, e.draft_created_at FROM emails e JOIN companies c ON c.id=e.company_id "
                  "WHERE c.domain=%s AND e.gmail_draft_id IS NOT NULL ORDER BY e.id DESC LIMIT 1", (domain,))
    result = {"status": "ERROR", "reason": ""}
    gmail_note = None
    db.x("UPDATE companies SET stage='finding_contact', updated_at=now() WHERE id=%s", (cid,))
    research.log(run_id, cid, "contact_step_started", {})
    try:
        try:
            hres = hunter_lookup(run_id, domain)
        except P.HunterPaused as e:
            result = {"status": "CONTACT_LOOKUP_PAUSED", "reason": f"Contact lookup paused: {e}. Qualified, but no contact searched yet."}
            research.log(run_id, cid, "hunter_paused", {"reason": str(e)}, "warn")
            raise StopIteration
        research.log(run_id, cid, "hunter_lookup", {"from_cache": hres["from_cache"], "credits_used": hres["credits_used"]})
        cands = P.build_candidates((hres["data"] or {}).get("emails") or [], domain)
        if not cands:
            result = {"status": "NO_SUITABLE_CONTACT", "reason": "Hunter returned no named people for this domain."}
            raise StopIteration
        scored, chosen = P.score_candidates(a.get("company_name") or domain, cands, usage)
        db.x("DELETE FROM contacts WHERE company_id=%s", (cid,))
        chosen_id = None
        for s in scored:
            c = cands[s["index"]]
            row = db.one("INSERT INTO contacts (company_id,name,position,department,seniority,hunter_confidence,email,role_category,scores,total,passed,chosen,reasoning,hunter_sources) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                         (cid, c["name"], c.get("position"), c.get("department"), c.get("seniority"), c.get("confidence"), c.get("email"), s["role_category"],
                          db.J(s["scores"]), s["total"], s["pass"], bool(chosen and chosen["index"] == s["index"]), s["reasoning"], db.J(c.get("source_links"))))
            if chosen and chosen["index"] == s["index"]:
                chosen_id = row["id"]
        research.log(run_id, cid, "contacts_scored", {"candidates": len(scored), "passing": len([s for s in scored if s["pass"]])})
        if not chosen:
            result = {"status": "NO_SUITABLE_CONTACT", "reason": "No Hunter candidate scored 8/10 or more with company link and role ownership."}
            raise StopIteration

        db.x("UPDATE companies SET stage='writing_email', updated_at=now() WHERE id=%s", (cid,))
        facts, hyps, ver = P.pain_points(a, sources, chosen, st, usage)
        research.log(run_id, cid, "pain_points", {"attempts": len(st.get("pain_attempts", [])), "passed": bool(hyps)})
        if not hyps:
            result = {"status": "INSUFFICIENT_PAIN_EVIDENCE", "reason": st.get("reason", "Could not write two supported pain hypotheses.")}
            raise StopIteration
        db.x("DELETE FROM pain_hypotheses WHERE company_id=%s", (cid,))
        for i, h in enumerate(hyps, 1):
            db.x("INSERT INTO pain_hypotheses (company_id,position,observed_fact,evidence_url,evidence_excerpt,hypothesis,role_relevance,possible_consequence,capability,scores,total,passed) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (cid, i, h["observed_fact"], h["evidence"]["source_url"], h["evidence"]["excerpt"], h["hypothesis"], h.get("role_relevance"),
                  h.get("possible_consequence"), h["capability"], db.J(h["scores"]), h["total"], h["pass"]))

        draft, qa_total = P.write_email(facts, ver, chosen, hyps[0], st, usage)
        db.x("DELETE FROM emails WHERE company_id=%s", (cid,))
        email_id = None
        for att in st.get("email_attempts", []):
            if "error" in att:
                continue
            row = db.one("INSERT INTO emails (company_id,contact_id,revision,subject,body,words,qa,qa_total,passed,is_current,original_subject,original_body) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                         (cid, chosen_id, att["revision"], att["subject"], att["body"], att["words"],
                          db.J({"scores": att["qa_scores"], "mandatory": att["mandatory"], "code_problems": att["code_problems"], "feedback": att["feedback"]}),
                          att["qa_total"], att["passed"], att["passed"], att["subject"] if att["passed"] else None, att["body"] if att["passed"] else None))
            if att["passed"]:
                email_id = row["id"]
        research.log(run_id, cid, "email_qa", {"attempts": len(st.get("email_attempts", [])), "passed": bool(draft), "qa_total": qa_total})
        if not draft:
            result = {"status": "EMAIL_QA_FAILED", "reason": st.get("reason", "The draft failed the quality checks after two revisions.")}
            raise StopIteration

        # deliver to Gmail Drafts at the same moment it is saved for the dashboard
        try:
            draft_id, verb = gmail_drafts.create_or_replace(draft["subject"], draft["body"], prev["gmail_draft_id"] if prev else None)
            created = prev["draft_created_at"] if (verb == "replaced" and prev) else dt.datetime.now(dt.timezone.utc)
            db.x("UPDATE emails SET gmail_draft_id=%s, draft_created_at=%s, draft_updated_at=now() WHERE id=%s", (draft_id, created, email_id))
            research.log(run_id, cid, "gmail_draft", {"draft_id": draft_id, "action": verb})
        except (gmail_drafts.GmailSetupError, gmail_drafts.GmailError) as e:
            gmail_note = str(e)[:400]
            db.x("UPDATE emails SET gmail_draft_error=%s WHERE id=%s", (gmail_note, email_id))
            research.log(run_id, cid, "gmail_draft_failed", {"error": gmail_note}, "error")
        result = {"status": "DRAFT_READY", "reason": "Draft ready for your review." + (f" Gmail draft NOT created: {gmail_note}" if gmail_note else "")}
    except StopIteration:
        pass
    except Exception as e:                                    # one company failing never stops the run
        result = {"status": "ERROR", "reason": f"{type(e).__name__}: {str(e)[:200]}"}
        research.log(run_id, cid, "company_error", {"error": result["reason"]}, "error")

    secs = round(time.time() - t0, 1)
    esc = None if result["status"] == "DRAFT_READY" else f"{result['reason']} {P.finish_text({'status': result['status']}) or ''}".strip()
    flags = list(co["flags"] or []) + (["gmail_draft_failed"] if gmail_note else [])
    db.x("""UPDATE companies SET status=%s, stage='done', reason=%s, escalation_reason=%s, flags=%s, usage=%s,
            seconds=COALESCE(seconds,0)+%s, finished_at=now(), updated_at=now() WHERE id=%s""",
         (result["status"], result["reason"], esc, flags, db.J(_merge_usage(co["usage"], usage)), secs, cid))
    research.log(run_id, cid, "company_finished", {"status": result["status"], "seconds": secs})
    return db.one("SELECT * FROM companies WHERE id=%s", (cid,))
