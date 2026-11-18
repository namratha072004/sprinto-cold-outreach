#!/usr/bin/env python3
"""Review desk for the Growth Lead. Run:  ./start.sh   (or  .venv/bin/python app.py)  then open http://127.0.0.1:8765
Reads the saved per-company results in output/<domain>/state.json. Nothing is sent without Approve."""
import os, re, threading, queue, time
from flask import Flask, jsonify, request, send_from_directory, abort
import settings as S
import pipeline as P
import rubric as R
import review, mailer, metrics

app = Flask(__name__, static_folder="static", static_url_path="/static")
DOMAIN_RX = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
JOBS = queue.Queue()
RUN = {"active": False, "current": None, "queued": []}
_HUNTER_CACHE = {"at": 0, "remaining": None}

# ---------------------------------------------------------------- safety for a local app
@app.before_request
def guard():
    if S.HOSTED:                                         # hosted copy is read-only: nothing can run, change or send
        if request.method == "POST":
            return jsonify({"error": "This hosted copy is read-only. Run, edit, approve and send work in the local app (./start.sh)."}), 403
        return None
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):          # blocks DNS-rebinding style access
        abort(403)
    if request.method == "POST" and not request.is_json:  # a web page on another site cannot send JSON without a CORS preflight
        abort(415)

def valid_domain(d):
    if not DOMAIN_RX.match(d or ""):
        abort(404)
    return d

# ---------------------------------------------------------------- background worker (one company at a time)
def worker():
    while True:
        batch = JOBS.get()
        RUN.update(active=True)
        def on_start(d):
            RUN["current"] = d
            RUN["queued"] = [x for x in RUN["queued"] if x != d]
        try:
            P.run_batch(batch, on_start=on_start)
        except Exception as e:                          # never let the worker die
            print("batch error:", type(e).__name__)
        RUN.update(active=JOBS.qsize() > 0, current=None)

if not S.HOSTED:
    threading.Thread(target=worker, daemon=True).start()

def recover_interrupted():
    """If the app was closed mid-run, say so in plain words instead of showing 'working' forever."""
    for st in S.all_states():
        if st.get("stage") not in (None, "done"):
            st.update(status="ERROR", stage="done", reason="The run was interrupted (app closed). Run this company again.",
                      escalation="The run was interrupted (app closed). Run this company again.")
            S.write_state(st)
if not S.HOSTED:
    recover_interrupted()

# ---------------------------------------------------------------- pages and data
@app.get("/")
def index():
    return send_from_directory("static", "index.html")

def summary(st):
    a = st.get("assessment") or {}
    v = review.view_status(st)
    return {"domain": st["domain"], "name": a.get("company_name") or st["domain"], "status": v["label"], "tone": v["tone"],
            "key": v["key"], "score": round(st["total"]) if "total" in st else None,
            "needs_review": v["key"] == "DRAFT_READY"}

@app.get("/api/companies")
def companies():
    rows = [summary(s) for s in S.all_states()]
    rows.sort(key=lambda r: (not r["needs_review"], r["domain"]))
    return jsonify(rows)

@app.get("/api/company/<domain>")
def company(domain):
    st = S.read_state(valid_domain(domain))
    if not st:
        abort(404)
    if not S.HOSTED:
        review.mark_opened(domain)
    st = S.read_state(domain)
    st["view"] = review.view_status(st)
    st["finish_text"] = P.finish_text(st)
    return jsonify(st)

@app.get("/api/config")
def config():
    return jsonify({"weights": R.WEIGHTS, "threshold": R.THRESHOLD_TOTAL, "contact_pass": R.CONTACT_PASS_TOTAL,
                    "words": [80, 120], "mail": ({"ready": False} if S.HOSTED else mailer.send_status()), "hosted": S.HOSTED, "targets": metrics.compute()["targets"]})

@app.get("/api/status")
def status():
    if not S.HOSTED and time.time() - _HUNTER_CACHE["at"] > 60 and S.env("HUNTER_API_KEY"):        # free account call, cached for a minute
        _HUNTER_CACHE.update(at=time.time(), remaining=P.hunter_remaining())
    return jsonify({"run": RUN, "hunter_remaining": _HUNTER_CACHE["remaining"], "mail": ({"ready": False} if S.HOSTED else mailer.send_status()),
                    "gemini_key_set": bool(S.env("GEMINI_API_KEY")), "hosted": S.HOSTED})

@app.get("/api/metrics")
def get_metrics():
    return jsonify(metrics.compute())

# ---------------------------------------------------------------- actions
@app.post("/api/run")
def run():
    data = request.get_json(silent=True) or {}
    if data.get("from_file"):
        path = os.path.join(S.BASE, "domains.txt")
        text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    else:
        text = data.get("domains", "")
    parsed = [P.clean_domain(l) for l in text.splitlines()]
    parsed = [d for d in parsed if d]
    bad = [d[8:] for d in parsed if d.startswith("INVALID:")]
    domains = list(dict.fromkeys(d for d in parsed if not d.startswith("INVALID:")))
    if not S.env("GEMINI_API_KEY"):
        return jsonify({"error": "GEMINI_API_KEY is empty in .env, so nothing can be read or written yet."}), 400
    if not domains:
        return jsonify({"error": "No valid domains found.", "invalid": bad}), 400
    existing = [d for d in domains if S.read_state(d)]
    if existing and not data.get("rerun"):
        return jsonify({"existing": existing, "message": "These already have results. Running again replaces them (Hunter results stay cached, so no extra credits)."}), 409
    for d in domains:
        S.write_state({"domain": d, "status": "QUEUED", "stage": "queued", "run_date": S.today()})
    RUN["queued"] += domains
    JOBS.put(domains)
    RUN["active"] = True
    return jsonify({"queued": domains, "invalid": bad})

def act(fn, *a, **k):
    try:
        st = fn(*a, **k)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if st is None:
        abort(404)
    st["view"] = review.view_status(st)
    return jsonify(st)

def body_fields():
    d = request.get_json(silent=True) or {}
    s, b = (d.get("subject") or "").strip(), (d.get("body") or "").strip()
    if not s or not b:
        abort(400)
    return s, b, d

@app.post("/api/company/<domain>/edit")
def edit(domain):
    s, b, _ = body_fields()
    return act(review.save_edit, valid_domain(domain), s, b)

@app.post("/api/company/<domain>/reject")
def reject(domain):
    s, b, d = body_fields()
    return act(review.reject, valid_domain(domain), s, b, d.get("reason", ""))

@app.post("/api/company/<domain>/approve")
def approve(domain):
    s, b, _ = body_fields()
    return act(review.approve, valid_domain(domain), s, b)

@app.post("/api/company/<domain>/retry-send")
def retry_send(domain):
    return act(review.retry_send, valid_domain(domain))

@app.post("/api/company/<domain>/contact-feedback")
def contact_feedback(domain):
    d = request.get_json(silent=True) or {}
    return act(review.contact_feedback, valid_domain(domain), bool(d.get("correct")))

@app.post("/api/company/<domain>/resume")
def resume(domain):
    valid_domain(domain)
    st = S.read_state(domain)
    if not st or st.get("status") != "CONTACT_LOOKUP_PAUSED":
        return jsonify({"error": "Only a paused contact lookup can be retried."}), 400
    def job():
        P.hunter_reset()
        P.resume_contact(domain)
    threading.Thread(target=job, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    os.makedirs(S.OUT, exist_ok=True)
    port = int(os.environ.get("PORT", 8765))             # not 5000: macOS AirPlay Receiver uses it
    print(f"Review desk: http://127.0.0.1:{port}   (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
