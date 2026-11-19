"""Tests every credential in .env. Prints PASS/FAIL only, never a secret. Nothing is created, sent or paid for:
Gemini = one tiny call, Hunter = the free /account call, Neon = SELECT 1, Gmail = token refresh only (no draft).
Run:  .venv/bin/python scripts/check_keys.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import settings as S

def shape(name):
    v = S.env(name) or ""
    n = sum(1 for l in open(os.path.join(S.BASE, ".env"), encoding="utf-8") if l.startswith(name + "="))
    return f"length {len(v)}" + (f", {n} lines in .env (should be 1)" if n != 1 else "")

def gemini():
    import pipeline as P
    u = {}
    P.gemini_json('Return JSON {"ok": true}', u)
    return f"model {u.get('model')} answered"

def hunter():
    r = requests.get("https://api.hunter.io/v2/account", headers={"X-API-KEY": S.env("HUNTER_API_KEY") or ""}, timeout=20)
    if not r.ok: raise RuntimeError(f"HTTP {r.status_code}")
    rq = r.json()["data"]["requests"]; c = rq.get("credits") or rq.get("searches") or {}
    return f"plan {r.json()['data'].get('plan_name')}, {c.get('remaining')} credits left, resets {r.json()['data'].get('reset_date')}"

def neon():
    import psycopg2
    with psycopg2.connect(S.env("DATABASE_URL") or "", connect_timeout=20) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM rubric_versions WHERE is_active"); return f"connected, {cur.fetchone()[0]} active rubric version"

def neon_api():
    r = requests.get("https://console.neon.tech/api/v2/projects", headers={"Authorization": "Bearer " + (S.env("NEON_API_KEY") or "")}, params={"limit": 50}, timeout=30)
    if not r.ok: raise RuntimeError(f"HTTP {r.status_code}")
    return f"{len(r.json().get('projects', []))} projects visible"

def gmail():
    r = requests.post("https://oauth2.googleapis.com/token", data={"client_id": S.env("GMAIL_OAUTH_CLIENT_ID"), "client_secret": S.env("GMAIL_OAUTH_CLIENT_SECRET"),
                      "refresh_token": S.env("GMAIL_OAUTH_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=30)
    j = r.json()
    if not r.ok: raise RuntimeError(f"{j.get('error')} ({j.get('error_description')})")
    p = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers={"Authorization": "Bearer " + j["access_token"]}, timeout=30)
    return f"token works, scope {j.get('scope')}, mailbox matches GMAIL_ADDRESS: {p.ok and p.json().get('emailAddress','').lower() == (S.env('GMAIL_ADDRESS') or '').lower()}"

checks = [("GEMINI_API_KEY", gemini), ("HUNTER_API_KEY", hunter), ("DATABASE_URL (Neon database)", neon),
          ("NEON_API_KEY", neon_api), ("GMAIL_OAUTH_* (Gmail drafts)", gmail)]
bad = 0
for name, fn in checks:
    key = name.split()[0].replace("GMAIL_OAUTH_*", "GMAIL_OAUTH_REFRESH_TOKEN")
    try:
        print(f"PASS  {name:32} {fn()}")
    except Exception as e:
        bad += 1; print(f"FAIL  {name:32} {str(e)[:150]}   [{shape(key)}]")
print("\nall keys pass" if not bad else f"\n{bad} of {len(checks)} failing")
