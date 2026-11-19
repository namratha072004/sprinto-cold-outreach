#!/usr/bin/env python3
"""Sprinto outreach desk. Run locally:  ./start.sh  then open http://127.0.0.1:8765   (also deployable on Vercel: this file exports `app`).
Everything is read from Neon. Approve / Edit / Reject / Done only RECORD her decision: the software has no send function and no send button.
Prospect email addresses are masked on the public site."""
import os, time, threading, datetime as dt
from flask import Flask, jsonify, request, send_from_directory, abort
import db, review, metrics, research, jobs, gmail_drafts, rubric_admin
import settings as S

from flask.json.provider import DefaultJSONProvider

class _Json(DefaultJSONProvider):
    """Dates leave the server as ISO text (2026-11-18, 2026-11-19T10:00:00+00:00), not as HTTP-date strings."""
    def default(self, o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        return super().default(o)

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.json = _Json(app)
_CACHE = {"hunter": (0, None), "gmail": (0, None)}
_RUN_LOCK = threading.Lock()
NOT_FIT = ("DISQUALIFIED", "LOW_PRIORITY", "INSUFFICIENT_EVIDENCE", "NO_SUITABLE_CONTACT", "INSUFFICIENT_PAIN_EVIDENCE", "EMAIL_QA_FAILED", "CONTACT_LOOKUP_PAUSED", "ERROR")
ARTICLE_LABEL = {"drafted": "Drafted", "researching": "Researching now", "no_website": "Website not confirmed", "error": "Error", "rejected": "Researched, did not fit",
                 "not_funding_news": "Not funding news", "merged": "Merged with another article", "too_old": "Too old", "outside_stage": "Outside Series A-C",
                 "not_shortlisted": "Not shortlisted", "shortlisted": "Shortlisted", "not_read": "Not read yet"}

# ---------------------------------------------------------------- privacy: prospects' addresses are masked on the public site
def mask(addr):
    return (addr[0] + "***@" + addr.split("@", 1)[1]) if addr and "@" in addr else addr
def hosted(): return bool(os.environ.get("VERCEL"))

@app.before_request
def guard():
    if not os.environ.get("VERCEL"):                      # locally: only this machine may talk to the app
        if (request.host or "").split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)
    if request.method == "POST" and not request.is_json:  # a page on another site cannot send JSON without a CORS preflight
        abort(415)

@app.errorhandler(ValueError)
def bad_input(e): return jsonify({"error": str(e)}), 400
@app.errorhandler(RuntimeError)
def runtime(e): return jsonify({"error": str(e)}), 500

@app.get("/")
def index(): return send_from_directory("static", "index.html")

@app.get("/api/me")
def me():
    return jsonify({"name": S.env("SENDER_NAME") or "there", "can_edit": True})

# ---------------------------------------------------------------- shared helpers
def _drafts_rows():
    rows = db.q("""SELECT DISTINCT ON (c.domain) c.id, c.name, c.domain, c.funding_stage, c.total_score, c.finished_at, c.run_id,
                   e.id email_id, e.subject, e.body, e.review_decision, e.qa_total, e.gmail_draft_id, e.gmail_message_id, e.gmail_draft_error, e.draft_created_at,
                   ct.name contact_name, ct.position contact_position, ct.total contact_total, ct.email contact_email, ct.email_verification
                   FROM companies c JOIN emails e ON e.company_id=c.id AND e.is_current LEFT JOIN contacts ct ON ct.company_id=c.id AND ct.chosen
                   WHERE c.domain IS NOT NULL ORDER BY c.domain, c.id DESC""")
    now = dt.datetime.now(dt.timezone.utc)
    for r in rows:
        r["kind"] = "none" if not r["contact_email"] else ("verified" if r["email_verification"] == "valid" else "unverified")
        created = r["draft_created_at"] or r["finished_at"]
        r["created_at"] = created
        r["older_than_24h"] = bool(created and (now - created).total_seconds() > 86400 and r["review_decision"] in ("pending", "edited"))
        body = (r["body"] or "").split("\n\n")
        r["snippet"] = (body[1] if len(body) > 1 else body[0])[:170]
        r["gmail_open_url"] = (f"https://mail.google.com/mail/u/0/#drafts?compose={r['gmail_message_id']}" if r["gmail_message_id"] else "https://mail.google.com/mail/u/0/#drafts")
    return rows

def _latest_run():
    return db.one("SELECT r.*, v.version FROM runs r JOIN rubric_versions v ON v.id=r.rubric_version_id "
                  "WHERE EXISTS (SELECT 1 FROM discovery_results d WHERE d.run_id=r.id) OR EXISTS (SELECT 1 FROM companies c WHERE c.run_id=r.id) ORDER BY r.started_at DESC LIMIT 1")

def _hunter_left():
    now = time.time()
    if now - _CACHE["hunter"][0] > 60 and S.env("HUNTER_API_KEY"):
        import pipeline as P
        _CACHE["hunter"] = (now, P.hunter_remaining())
    return _CACHE["hunter"][1]

def _gmail_health():
    now = time.time()
    if now - _CACHE["gmail"][0] > 300:
        try:
            gmail_drafts.access_token(); _CACHE["gmail"] = (now, "ok")
        except Exception as e:
            _CACHE["gmail"] = (now, str(e)[:240])
    return _CACHE["gmail"][1]

# ---------------------------------------------------------------- Today
@app.get("/api/overview")
def overview():
    run = _latest_run()
    drafts = _drafts_rows()
    out = {"name": S.env("SENDER_NAME") or "there", "to_review": len([d for d in drafts if d["review_decision"] in ("pending", "edited")]),
           "run": None, "hunter_left": _hunter_left(), "gmail": _gmail_health(),
           "waiting": [{"id": d["id"], "name": d["name"], "contact": d["contact_name"], "score": round(float(d["total_score"] or 0))}
                       for d in drafts if d["review_decision"] in ("pending", "edited")][:5]}
    if run:
        c = db.one("""SELECT count(*) n, count(*) FILTER (WHERE status = ANY(%s)) skipped, count(*) FILTER (WHERE stage<>'done') unfinished,
                      count(*) FILTER (WHERE status='DRAFT_READY') drafted FROM companies WHERE run_id=%s""", (list(NOT_FIT), run["id"]))
        a = db.one("SELECT count(*) articles, count(DISTINCT query) sources FROM discovery_results WHERE run_id=%s", (run["id"],))
        out["run"] = {"id": run["id"], "started_at": run["started_at"], "finished_at": run["finished_at"], "status": run["status"], "version": run["version"],
                      "articles": a["articles"], "sources": a["sources"], "companies": c["n"], "skipped": c["skipped"], "unfinished": c["unfinished"], "drafted": c["drafted"]}
    return jsonify(out)

@app.post("/api/run")
def run():
    """Press Run: creates a run in Neon, searches Google News, builds the shortlist. Returns the companies the browser then advances one short step at a time."""
    if not S.env("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not set.")
    if not _RUN_LOCK.acquire(blocking=False):
        return jsonify({"error": "A run is already starting. Please wait a moment."}), 409
    try:
        rid, found, sl = jobs.start("dashboard")
        return jsonify({"run_id": str(rid), **found, "shortlisted": sl["kept"], "company_ids": sl["company_ids"], "outcomes": sl["outcomes"]})
    finally:
        _RUN_LOCK.release()

@app.get("/api/run/open")
def run_open():
    """Companies of the latest run that are not finished (so a closed tab can continue)."""
    run = _latest_run()
    if not run:
        return jsonify({"run_id": None, "company_ids": []})
    ids = [r["id"] for r in db.q("SELECT id FROM companies WHERE run_id=%s AND stage<>'done' AND updated_at > now() - interval '3 hours' ORDER BY shortlist_rank NULLS LAST, id", (run["id"],))]
    return jsonify({"run_id": str(run["id"]), "company_ids": ids})

@app.get("/api/run/progress")
def run_progress():
    run = _latest_run()
    if not run:
        return jsonify([])
    return jsonify(db.q("SELECT id, name, domain, status, stage, total_score FROM companies WHERE run_id=%s ORDER BY shortlist_rank NULLS LAST, id", (run["id"],)))

@app.post("/api/advance")
def advance():
    cid = (request.get_json(silent=True) or {}).get("company_id")
    if not isinstance(cid, int):
        raise ValueError("company_id is required.")
    return jsonify(jobs.advance(cid))

# ---------------------------------------------------------------- Drafts
@app.get("/api/drafts")
def drafts():
    rows = _drafts_rows()
    tab, kind = request.args.get("tab", "review"), request.args.get("kind")
    def in_tab(r, t):
        d = r["review_decision"]
        return {"review": d in ("pending", "edited"), "approved": d == "approved", "done": d == "done", "older": r["older_than_24h"]}[t]
    counts = {t: len([r for r in rows if in_tab(r, t)]) for t in ("review", "approved", "done", "older")}
    tabbed = [r for r in rows if in_tab(r, tab)] if tab in counts else []
    kinds = {k: len([r for r in tabbed if r["kind"] == k]) for k in ("verified", "unverified", "none")}
    shown = [r for r in tabbed if not kind or r["kind"] == kind]
    shown.sort(key=lambda r: r["created_at"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    items = [{"id": r["id"], "name": r["name"], "domain": r["domain"], "subject": r["subject"], "snippet": r["snippet"], "decision": r["review_decision"], "kind": r["kind"],
              "contact": r["contact_name"], "position": r["contact_position"], "contact_score": r["contact_total"], "score": round(float(r["total_score"] or 0)),
              "funding_stage": r["funding_stage"], "created_at": r["created_at"], "in_gmail": bool(r["gmail_draft_id"] and not r["gmail_draft_error"]),
              "gmail_error": r["gmail_draft_error"], "gmail_open_url": r["gmail_open_url"], "older_than_24h": r["older_than_24h"]} for r in shown]
    return jsonify({"counts": counts, "kinds": kinds, "items": items})

@app.get("/api/company/<int:cid>")
def company(cid):
    c = db.one("SELECT * FROM companies WHERE id=%s", (cid,))
    if not c:
        abort(404)
    review.mark_opened(cid)
    email = db.one("SELECT * FROM emails WHERE company_id=%s AND is_current", (cid,))
    attempts = db.q("SELECT revision, words, qa_total, passed, qa FROM emails WHERE company_id=%s ORDER BY revision", (cid,))
    run = db.one("SELECT r.id, r.started_at, v.version FROM runs r JOIN rubric_versions v ON v.id=r.rubric_version_id WHERE r.id=%s", (c["run_id"],))
    v = review.view(c, email["review_decision"] if email and email["review_decision"] != "pending" else None)
    contacts = db.q("SELECT id, name, position, role_category, total, passed, chosen, scores, reasoning, hunter_sources, contact_correct, email, email_verification FROM contacts WHERE company_id=%s ORDER BY chosen DESC, total DESC", (cid,))
    if hosted():
        for x in contacts:
            x["email"] = mask(x["email"])
    if email:
        email["gmail_open_url"] = (f"https://mail.google.com/mail/u/0/#drafts?compose={email['gmail_message_id']}" if email.get("gmail_message_id") else "https://mail.google.com/mail/u/0/#drafts")
    return jsonify({
        "can_edit": True,
        "company": {k: c[k] for k in ("id", "domain", "name", "status", "stage", "funding_stage", "funding_date", "total_score", "gates", "flags", "reason", "escalation_reason",
                                      "research_limit_hit", "seconds", "retry_counts", "usage", "overruled", "overrule_note")},
        "view": v, "run": run, "overrule_allowed": bool(c["assessment"] is not None and c["status"] in NOT_FIT and not c["overruled"]),
        "scores": db.q("SELECT dimension, level, points, state, reasoning FROM qualification_scores WHERE company_id=%s ORDER BY points DESC, dimension", (cid,)),
        "evidence": db.q("SELECT dimension, claim, source_kind, source_url, excerpt, retrieved_on, published_on, label, round_no FROM evidence WHERE company_id=%s ORDER BY dimension, id", (cid,)),
        "rounds": db.q("SELECT round_no, gap, sources, new_evidence, revised_decision FROM research_rounds WHERE company_id=%s ORDER BY round_no", (cid,)),
        "contacts": contacts,
        "pain": db.q("SELECT position, observed_fact, evidence_url, evidence_excerpt, hypothesis, role_relevance, possible_consequence, capability, total FROM pain_hypotheses WHERE company_id=%s ORDER BY position", (cid,)),
        "email": email, "email_attempts": attempts,
        "found_via": db.q("SELECT headline, source_name, source_url, published_at, query, outcome_note, domain_evidence FROM discovery_results WHERE company_id=%s OR (company_domain=%s AND %s IS NOT NULL) ORDER BY id DESC LIMIT 3", (cid, c["domain"], c["domain"])),
        "words": [80, 120], "gmail_drafts_url": "https://mail.google.com/mail/u/0/#drafts"})

# ---------------------------------------------------------------- Rejected
@app.get("/api/rejected")
def rejected():
    status, window = request.args.get("status"), request.args.get("window", "all")
    rows = db.q("""SELECT DISTINCT ON (COALESCE(c.domain, c.id::text)) c.id, c.name, c.domain, c.status, c.reason, c.escalation_reason, c.total_score, c.funding_stage, c.flags,
                   c.finished_at, c.assessment IS NOT NULL AS researched, d.published_at, d.source_name, d.source_url, d.headline
                   FROM companies c LEFT JOIN discovery_results d ON d.company_id=c.id
                   WHERE c.status = ANY(%s) AND c.overruled=false AND c.stage='done' ORDER BY COALESCE(c.domain, c.id::text), c.id DESC""", (list(NOT_FIT),))
    now = dt.date.today()
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    def keep(r):
        if status and r["status"] != status:
            return False
        if window in ("24h", "7d") and r["published_at"]:
            return (now - r["published_at"]).days <= (1 if window == "24h" else 7)
        return window == "all" or bool(r["published_at"])
    items = [{"id": r["id"], "name": r["name"], "domain": r["domain"], "status": r["status"], "label": review.STATUS_VIEW.get(r["status"], (r["status"],))[0],
              "reason": r["escalation_reason"] or r["reason"], "score": round(float(r["total_score"])) if r["total_score"] is not None else None, "funding_stage": r["funding_stage"],
              "announced": r["published_at"], "source": r["source_name"], "source_url": r["source_url"], "headline": r["headline"],
              "overrule_allowed": bool(r["researched"] and r["status"] != "ERROR")} for r in rows if keep(r)]
    return jsonify({"counts": counts, "items": items})

# ---------------------------------------------------------------- Sources
@app.get("/api/sources")
def sources():
    run = db.one("SELECT r.id, r.started_at FROM runs r WHERE EXISTS (SELECT 1 FROM discovery_results d WHERE d.run_id=r.id) ORDER BY r.started_at DESC LIMIT 1")
    if not run:
        return jsonify({"run": None, "feeds": [], "items": [], "outcomes": {}})
    rows = db.q("""SELECT d.id, d.query, d.headline, d.source_name, d.source_url, d.published_at, d.company_name, d.company_domain, d.domain_status, d.outcome, d.outcome_note, d.company_id,
                   c.status c_status, c.stage c_stage FROM discovery_results d LEFT JOIN companies c ON c.id=d.company_id WHERE d.run_id=%s ORDER BY d.published_at DESC NULLS LAST, d.id""", (run["id"],))
    def state(r):
        if r["company_id"]:
            if r["c_status"] == "DRAFT_READY": return "drafted"
            if r["c_stage"] != "done": return "researching"
            if r["outcome"] == "no_website": return "no_website"
            return "error" if r["c_status"] == "ERROR" else "rejected"
        return r["outcome"] or "not_read"
    feeds, outcomes = {}, {}
    for r in rows:
        r["state"] = state(r); r["state_label"] = ARTICLE_LABEL.get(r["state"], r["state"])
        f = feeds.setdefault(r["query"], {"query": r["query"], "articles": 0, "named": 0, "states": {}})
        f["articles"] += 1; f["named"] += 1 if r["company_name"] else 0; f["states"][r["state"]] = f["states"].get(r["state"], 0) + 1
        outcomes[r["state"]] = outcomes.get(r["state"], 0) + 1
    q, st = request.args.get("query"), request.args.get("state")
    shown = [r for r in rows if (not q or r["query"] == q) and (not st or r["state"] == st)]
    return jsonify({"run": run, "feeds": list(feeds.values()), "outcomes": {k: {"count": v, "label": ARTICLE_LABEL.get(k, k)} for k, v in outcomes.items()},
                    "items": [{k: r[k] for k in ("headline", "source_name", "source_url", "published_at", "company_name", "company_domain", "domain_status", "state", "state_label", "outcome_note", "query")} for r in shown[:300]],
                    "total": len(rows), "shown": min(len(shown), 300)})

# ---------------------------------------------------------------- Runs and health
@app.get("/api/runs")
def runs():
    rows = db.q("""SELECT r.id, r.started_at, r.finished_at, r.status, r.hunter_calls, r.hunter_credits, r.hunter_paused, r.params, v.version,
                   (SELECT count(*) FROM discovery_results d WHERE d.run_id=r.id) articles,
                   (SELECT count(*) FROM companies c WHERE c.run_id=r.id) companies,
                   (SELECT count(*) FROM companies c WHERE c.run_id=r.id AND c.status='DRAFT_READY') drafts,
                   (SELECT count(*) FROM companies c WHERE c.run_id=r.id AND c.status='ERROR') errors,
                   (SELECT count(*) FROM companies c WHERE c.run_id=r.id AND c.stage<>'done') unfinished,
                   (SELECT COALESCE(sum(seconds),0) FROM companies c WHERE c.run_id=r.id) company_seconds
                   FROM runs r JOIN rubric_versions v ON v.id=r.rubric_version_id ORDER BY r.started_at DESC LIMIT 20""")
    problems = db.q("SELECT at, level, event, detail, run_id, company_id FROM run_log WHERE level IN ('error','warn') ORDER BY id DESC LIMIT 25")
    t0 = time.time(); db.one("SELECT 1"); latency = round((time.time() - t0) * 1000)
    import sprinto_context
    try:
        ctx = sprinto_context.load()["sha256"][:10]
    except Exception as e:
        ctx = None
    active = db.one("SELECT version FROM rubric_versions WHERE is_active")
    return jsonify({"runs": rows, "problems": problems, "metrics": metrics.compute(),
                    "health": {"database_ms": latency, "gmail": _gmail_health(), "hunter_left": _hunter_left(), "gemini_key": bool(S.env("GEMINI_API_KEY")),
                               "rubric": active["version"] if active else None, "context": ctx, "model": S.env("GEMINI_MODEL")}})

@app.get("/api/metrics")
def get_metrics(): return jsonify(metrics.compute())

# ---------------------------------------------------------------- Settings (rubric editor)
@app.get("/api/settings")
def settings_get(): return jsonify(rubric_admin.current())

@app.post("/api/settings")
def settings_save():
    name = rubric_admin.save(request.get_json(silent=True) or {})
    return jsonify({"saved_as": name, "settings": rubric_admin.current()})

@app.get("/api/config")
def config():
    cfg = research.ensure_rubric()
    return jsonify({"weights": cfg["weights"], "threshold": cfg["thresholds"]["total_min"], "contact_pass": cfg["thresholds"]["contact_pass_total"],
                    "words": [int(cfg["thresholds"]["email_words_target_min"]), int(cfg["thresholds"]["email_words_target_max"])],
                    "rubric_version": cfg["version"], "labels": {d["key"]: d["label"] for d in cfg["dimensions"]}})

# ---------------------------------------------------------------- review actions: they only RECORD her decision (or update the Gmail draft)
def _act(cid, fn, *a):
    out = fn(cid, *a)
    r = company(cid).get_json(); r["message"] = out if isinstance(out, str) else None
    return jsonify(r)

@app.post("/api/company/<int:cid>/approve")
def approve(cid): return _act(cid, review.approve)

@app.post("/api/company/<int:cid>/done")
def done(cid): return _act(cid, review.mark_done)

@app.post("/api/company/<int:cid>/reject")
def reject(cid): return _act(cid, review.reject, (request.get_json(silent=True) or {}).get("reason", ""))

@app.post("/api/company/<int:cid>/restore")
def restore(cid): return _act(cid, review.restore_in_gmail)

@app.post("/api/company/<int:cid>/edit")
def edit(cid):
    d = request.get_json(silent=True) or {}
    gmail_error = review.edit(cid, d.get("subject") or "", d.get("body") or "")
    out = company(cid).get_json(); out["gmail_error"] = gmail_error
    return jsonify(out)

@app.post("/api/company/<int:cid>/contact-feedback")
def contact_feedback(cid):
    review.contact_feedback(cid, bool((request.get_json(silent=True) or {}).get("correct")))
    return company(cid)

@app.post("/api/company/<int:cid>/overrule")
def overrule(cid):
    review.overrule(cid, (request.get_json(silent=True) or {}).get("note", ""))
    return company(cid)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))             # not 5000: macOS AirPlay Receiver uses it
    print(f"Sprinto outreach desk: http://127.0.0.1:{port}   (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
