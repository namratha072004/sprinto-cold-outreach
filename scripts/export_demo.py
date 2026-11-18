"""Copies the real saved results (output/) into demo_results/ for the hosted read-only copy.
Prospect email addresses are masked (j***@company.com) because the repo is public. Names and job titles stay:
they are what 'why this person' is built on. Run:  .venv/bin/python scripts/export_demo.py"""
import os, sys, json, re, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC, DST = os.path.join(BASE, "output"), os.path.join(BASE, "demo_results")

def mask(addr):
    if not addr or "@" not in addr:
        return addr
    local, dom = addr.split("@", 1)
    return local[0] + "***@" + dom

shutil.rmtree(DST, ignore_errors=True)
n = 0
for d in sorted(os.listdir(SRC)):
    p = os.path.join(SRC, d, "state.json")
    if d == "cache" or not os.path.exists(p):
        continue
    st = json.load(open(p, encoding="utf-8"))
    if st.get("status") in ("QUEUED", "STARTED") or "TEST FIXTURE" in json.dumps(st):
        continue                                           # only finished, real results
    for c in st.get("hunter_candidates", []):
        c["email"] = mask(c.get("email"))
    if st.get("contact"):
        st["contact"]["email"] = mask(st["contact"].get("email"))
    os.makedirs(os.path.join(DST, d), exist_ok=True)
    json.dump(st, open(os.path.join(DST, d, "state.json"), "w", encoding="utf-8"), indent=1)
    n += 1
left = [m for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", json.dumps([json.load(open(os.path.join(DST, x, "state.json"))) for x in os.listdir(DST)])) if "***" not in m]
print(f"exported {n} companies to demo_results/ | unmasked email addresses left: {len(left)} {left[:3]}")
