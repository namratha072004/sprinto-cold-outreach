"""A Run is a queue of SHORT jobs, each saved in Neon, so every request fits Vercel's time limit and a closed tab loses nothing.
start()    -> creates the run, searches Google News, builds the shortlist (one request).
advance(id)-> runs the NEXT step of one company: finding_website -> reading_website -> finding_contact -> done.
The browser (or run_all.py) calls advance repeatedly. Plain code decides; nothing here can send mail."""
import time
import db, research, discovery, shortlist, website_finder, outreach
import settings as S
import pipeline as P

def start(trigger="dashboard"):
    research.ensure_rubric()
    rid = research.start_run({"trigger": trigger})
    found = discovery.discover(rid)
    sl = shortlist.build(rid)
    return rid, found, sl

def _claim(cid):
    """Only one worker may run a step for a company at a time (a stale claim older than 5 minutes is taken over)."""
    return bool(db.x("UPDATE companies SET claimed_at=now() WHERE id=%s AND stage<>'done' AND (claimed_at IS NULL OR claimed_at < now() - interval '5 minutes')", (cid,)))

def _release(cid, secs):
    db.x("UPDATE companies SET claimed_at=NULL, seconds=COALESCE(seconds,0)+%s, updated_at=now() WHERE id=%s", (secs, cid))

def advance(cid):
    """Runs one step. Returns {id, stage, status, step, ran}."""
    co = db.one("SELECT * FROM companies WHERE id=%s", (cid,))
    if not co:
        raise ValueError("No such company.")
    if co["stage"] == "done":
        return {"id": cid, "stage": "done", "status": co["status"], "step": None, "ran": False}
    if not _claim(cid):
        return {"id": cid, "stage": co["stage"], "status": co["status"], "step": None, "ran": False, "busy": True}
    research.ensure_rubric()
    t0, stage = time.time(), co["stage"]
    try:
        if stage == "finding_website":
            _website_step(co)
            step = "website"
        elif stage in ("reading_website",):
            research.research_company(co["run_id"], co["domain"], cid=cid)
            step = "research"
        else:                                                   # finding_contact / writing_email (also a retry after a cut-off)
            outreach.finish_company(co["run_id"], co["domain"])
            step = "contact_and_draft"
    except Exception as e:                                      # one company failing never stops the run
        db.x("UPDATE companies SET status='ERROR', stage='done', reason=%s, escalation_reason=%s, updated_at=now() WHERE id=%s",
             (f"{type(e).__name__}: {str(e)[:200]}", f"{type(e).__name__}: {str(e)[:200]} {P.finish_text({'status': 'ERROR'})}", cid))
        research.log(co["run_id"], cid, "company_error", {"error": str(e)[:200]}, "error")
        step = "error"
    _release(cid, round(time.time() - t0, 1))
    research.close_run_if_done(co["run_id"])
    after = db.one("SELECT stage, status FROM companies WHERE id=%s", (cid,))
    return {"id": cid, "stage": after["stage"], "status": after["status"], "step": step, "ran": True}

def _website_step(co):
    row = db.one("SELECT d.headline FROM discovery_results d WHERE d.company_id=%s ORDER BY d.id LIMIT 1", (co["id"],))
    usage = {}
    res = website_finder.find(co["name"], row["headline"] if row else co["name"], usage)
    research.log(co["run_id"], co["id"], "website_lookup", {"ok": res["ok"], "why": res.get("why"), "domain": res.get("domain")}, "info" if res["ok"] else "warn")
    if not res["ok"]:
        msg = f"This company's website could not be confirmed: {res['why']}. A website is never guessed, so it was not researched."
        db.x("UPDATE companies SET status='INSUFFICIENT_EVIDENCE', stage='done', flags=flags || ARRAY['website_unconfirmed'], reason=%s, escalation_reason=%s, "
             "usage=%s, finished_at=now() WHERE id=%s", (msg, msg + " " + P.finish_text({"status": "INSUFFICIENT_EVIDENCE"}), db.J(usage), co["id"]))
        db.x("UPDATE discovery_results SET outcome='no_website', outcome_note=%s WHERE company_id=%s", (res["why"], co["id"]))
        return
    note = f"Verified on its own homepage ({res['url']}): \"{res['excerpt'][:140]}\""
    db.x("UPDATE companies SET domain=%s, stage='reading_website', usage=%s WHERE id=%s", (res["domain"], db.J(usage), co["id"]))
    db.x("UPDATE discovery_results SET company_domain=%s, domain_status='confirmed', domain_evidence=%s WHERE company_id=%s", (res["domain"], note, co["id"]))
    db.x("INSERT INTO evidence (company_id,dimension,claim,source_kind,source_url,excerpt,retrieved_on,label) VALUES (%s,'website',%s,'website',%s,%s,%s,'observed')",
         (co["id"], f"Website confirmed: {res['domain']}", res["url"], res["excerpt"], res["retrieved_on"]))
