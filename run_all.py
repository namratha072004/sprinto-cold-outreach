"""Command-line Run: discovery -> shortlist -> for each shortlisted company: website -> research -> contact -> email -> Gmail draft.
Same steps the dashboard's Run button drives (jobs.start / jobs.advance). Companies are processed 3 at a time.
Usage:  .venv/bin/python -u run_all.py"""
import sys, time, concurrent.futures as cf
import db, jobs

def drive(cid):
    while True:
        r = jobs.advance(cid)
        co = db.one("SELECT name, domain, status, stage, total_score FROM companies WHERE id=%s", (cid,))
        print(f"  [{time.strftime('%H:%M:%S')}] {co['name'][:26]:26} step={r.get('step')} -> {co['stage']:16} {co['status']}", flush=True)
        if r["stage"] == "done" or not r.get("ran"):
            return r

def main(workers=3):
    t0 = time.time()
    rid, found, sl = jobs.start("run_all.py")
    print(f"run {rid}: {found} | shortlist kept {sl['kept']} | outcomes {sl['outcomes']}", flush=True)
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(drive, sl["company_ids"]))
    db.x("UPDATE runs SET status='DONE', finished_at=now() WHERE id=%s", (rid,))
    print(f"DONE in {round(time.time() - t0)}s", flush=True)
    for r in db.q("SELECT name, domain, status, total_score, seconds FROM companies WHERE run_id=%s ORDER BY shortlist_rank", (rid,)):
        print(f"  {r['name'][:26]:26} {str(r['domain'] or '-'):24} {r['status']:24} score={r['total_score']} {r['seconds']}s")

if __name__ == "__main__":
    main()
