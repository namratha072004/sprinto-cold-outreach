#!/usr/bin/env python3
"""Review desk. Run locally:  ./start.sh  then open http://127.0.0.1:8765   (also deployable on Vercel: this file exports `app`).
Reads everything from Neon. Approve / Edit / Reject only RECORD her decision; the software has no send function and no send button.
The Run button starts a run in Neon, searches Google News, and saves what it finds."""
import os, time, threading
from flask import Flask, jsonify, request, send_from_directory, abort
import db, review, metrics, research, discovery, gmail_drafts
import settings as S
import rubric as R

app = Flask(__name__, static_folder="static", static_url_path="/static")
_CACHE = {"hunter": (0, None), "gmail": (0, None)}
_RUN_LOCK = threading.Lock()

@app.before_request
def guard():
    if not os.environ.get("VERCEL"):                      # locally: only this machine may talk to the app
        if (request.host or "").split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)
    if request.method == "POST" and not request.is_json:  # a web page on another site cannot send JSON without a CORS preflight
        abort(415)

@app.errorhandler(ValueError)
def bad_input(e):
    return jsonify({"error": str(e)}), 400

@app.errorhandler(RuntimeError)
def runtime(e):
    return jsonify({"error": str(e)}), 500

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

# ---------------------------------------------------------------- list and detail
@app.get("/api/companies")
def companies():
    rows = db.q("""SELECT DISTINCT ON (c.domain) c.id, c.domain, c.name, c.status, c.stage, c.total_score, c.updated_at, e.review_decision
                   FROM companies c LEFT JOIN emails e ON e.company_id=c.id AND e.is_current
                   ORDER BY c.domain, c.id DESC""")
    out = []
    for r in rows:
        v = review.view(r, r["review_decision"] if r["review_decision"] != "pending" else None)
        out.append({"id": r["id"], "domain": r["domain"], "name": r["name"] or r["domain"], "status": v["label"], "tone": v["tone"], "key": v["key"],
                    "score": round(float(r["total_score"])) if r["total_score"] is not None else None, "needs_review": v["key"] == "DRAFT_READY"})
    out.sort(key=lambda r: (not r["needs_review"], r["name"].lower()))
    return jsonify(out)

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
    sources = {s["url"]: s["kind"] for s in (c["sources_used"] or [])}
    return jsonify({
        "company": {k: c[k] for k in ("id", "domain", "name", "status", "stage", "funding_stage", "funding_date", "total_score", "gates", "flags",
                                      "reason", "escalation_reason", "research_limit_hit", "seconds", "retry_counts", "usage")},
        "view": v, "run": run, "sources_read": sources,
        "scores": db.q("SELECT dimension, level, points, state, reasoning FROM qualification_scores WHERE company_id=%s ORDER BY points DESC, dimension", (cid,)),
        "evidence": db.q("SELECT dimension, claim, source_kind, source_url, excerpt, retrieved_on, published_on, label, round_no FROM evidence WHERE company_id=%s ORDER BY dimension, id", (cid,)),
        "rounds": db.q("SELECT round_no, gap, sources, new_evidence, revised_decision FROM research_rounds WHERE company_id=%s ORDER BY round_no", (cid,)),
        "contacts": db.q("SELECT id, name, position, role_category, total, passed, chosen, scores, reasoning, hunter_sources, contact_correct, email FROM contacts WHERE company_id=%s ORDER BY chosen DESC, total DESC", (cid,)),
        "pain": db.q("SELECT position, observed_fact, evidence_url, evidence_excerpt, hypothesis, role_relevance, possible_consequence, capability, total FROM pain_hypotheses WHERE company_id=%s ORDER BY position", (cid,)),
        "email": email, "email_attempts": attempts,
        "found_via": db.q("SELECT headline, source_name, source_url, published_at, query FROM discovery_results WHERE company_domain=%s ORDER BY id DESC LIMIT 3", (c["domain"],)),
        "words": [80, 120], "gmail_drafts_url": "https://mail.google.com/mail/u/0/#drafts"})

@app.get("/api/config")
def config():
    cfg = research.ensure_rubric()
    return jsonify({"weights": cfg["weights"], "threshold": cfg["thresholds"]["total_min"], "contact_pass": cfg["thresholds"]["contact_pass_total"],
                    "words": [int(cfg["thresholds"]["email_words_target_min"]), int(cfg["thresholds"]["email_words_target_max"])],
                    "rubric_version": cfg["version"], "labels": {d["key"]: d["label"] for d in cfg["dimensions"]}})

# ---------------------------------------------------------------- run and discovery
@app.post("/api/run")
def run():
    """Creates a run in Neon, searches Google News with the queries stored in Neon, saves every result.
    (The shortlist step is waiting for the Growth Lead's OK, so the run stops after discovery.)"""
    if not S.env("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not set, so company names cannot be read from headlines.")
    if not _RUN_LOCK.acquire(blocking=False):
        return jsonify({"error": "A run is already in progress. Please wait for it to finish."}), 409
    try:
        research.ensure_rubric()
        rid = research.start_run({"trigger": "dashboard"})
        out = discovery.discover(rid)
        db.x("UPDATE runs SET status='DONE', finished_at=now() WHERE id=%s", (rid,))
        s = db.one("SELECT count(*) n, count(*) FILTER (WHERE domain_status='confirmed') conf, count(*) FILTER (WHERE company_name IS NOT NULL) named FROM discovery_results WHERE run_id=%s", (rid,))
        return jsonify({"run_id": str(rid), "results": s["n"], "domains_confirmed": s["conf"], "names_read": s["named"], "shortlist": "waiting for your OK", **out})
    finally:
        _RUN_LOCK.release()

@app.get("/api/discovery")
def discovery_latest():
    r = db.one("SELECT r.id, r.started_at FROM runs r WHERE EXISTS (SELECT 1 FROM discovery_results d WHERE d.run_id=r.id) ORDER BY r.started_at DESC LIMIT 1")
    if not r:
        return jsonify({"run": None, "results": []})
    return jsonify({"run": r, "results": db.q("SELECT headline, source_name, source_url, published_at, company_name, company_domain, domain_status, domain_evidence, query "
                                              "FROM discovery_results WHERE run_id=%s ORDER BY published_at DESC NULLS LAST, id", (r["id"],))})

# ---------------------------------------------------------------- status and scorecard
@app.get("/api/status")
def status():
    now = time.time()
    if now - _CACHE["hunter"][0] > 60 and S.env("HUNTER_API_KEY"):
        import pipeline as P
        _CACHE["hunter"] = (now, P.hunter_remaining())
    if now - _CACHE["gmail"][0] > 300:
        try:
            gmail_drafts.access_token(); _CACHE["gmail"] = (now, "ok")
        except Exception as e:
            _CACHE["gmail"] = (now, str(e)[:200])
    return jsonify({"hunter_remaining": _CACHE["hunter"][1], "gmail": _CACHE["gmail"][1], "gemini_key_set": bool(S.env("GEMINI_API_KEY"))})

@app.get("/api/metrics")
def get_metrics():
    return jsonify(metrics.compute())

# ---------------------------------------------------------------- review actions: they only RECORD her decision
@app.post("/api/company/<int:cid>/approve")
def approve(cid):
    review.approve(cid)
    return company(cid)

@app.post("/api/company/<int:cid>/reject")
def reject(cid):
    review.reject(cid, (request.get_json(silent=True) or {}).get("reason", ""))
    return company(cid)

@app.post("/api/company/<int:cid>/edit")
def edit(cid):
    d = request.get_json(silent=True) or {}
    gmail_error = review.edit(cid, d.get("subject") or "", d.get("body") or "")
    out = company(cid).get_json()
    out["gmail_error"] = gmail_error
    return jsonify(out)

@app.post("/api/company/<int:cid>/contact-feedback")
def contact_feedback(cid):
    review.contact_feedback(cid, bool((request.get_json(silent=True) or {}).get("correct")))
    return company(cid)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))             # not 5000: macOS AirPlay Receiver uses it
    print(f"Review desk: http://127.0.0.1:{port}   (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
