"""Uploads your LOCAL results (output/) to the hosted database: company states, page text, and Hunter lookups,
so re-running those companies online costs no extra Hunter credits. Needs DATABASE_URL in .env.
Usage: .venv/bin/python scripts/push_to_cloud.py"""
import os, sys, json
os.environ["SPRINTO_USE_DB"] = "1"          # send reads/writes to the database, not files
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import settings as S
SRC = os.path.join(S.BASE, "output")
n = 0
for d in sorted(os.listdir(SRC)):
    sp = os.path.join(SRC, d, "state.json")
    if d == "cache" or not os.path.exists(sp):
        continue
    st = json.load(open(sp, encoding="utf-8"))
    if st.get("stage") != "done" or st.get("status") in ("QUEUED", "STARTED"):
        continue
    S.kv_put("state", d, st); n += 1
    src = os.path.join(SRC, d, "sources.json")
    if os.path.exists(src):
        S.kv_put("sources", d, json.load(open(src, encoding="utf-8")))
h = 0
cache = os.path.join(SRC, "cache")
for f in os.listdir(cache) if os.path.isdir(cache) else []:
    if f.startswith("hunter_") and f.endswith(".json"):
        S.kv_put("cache", f[:-5], json.load(open(os.path.join(cache, f), encoding="utf-8"))); h += 1
print(f"uploaded {n} company results and {h} cached Hunter lookups")
