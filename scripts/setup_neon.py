"""Creates (or reuses) a Neon project called 'sprinto-outreach', writes its POOLED connection string into .env as
DATABASE_URL (never printed), and creates the kv table. Needs NEON_API_KEY in .env.
Usage: .venv/bin/python scripts/setup_neon.py"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import settings as S

NAME, BASE = "sprinto-outreach", "https://console.neon.tech/api/v2"
key = S.env("NEON_API_KEY")
if not key:
    sys.exit("NEON_API_KEY is empty in .env. Paste it there first.")
H = {"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"}

def call(method, path, **kw):
    r = requests.request(method, BASE + path, headers=H, timeout=60, **kw)
    if r.status_code >= 300:
        sys.exit(f"Neon API {method} {path} -> {r.status_code}: {r.text[:200]}")
    return r.json()

found = [p for p in call("GET", "/projects", params={"search": NAME, "limit": 20}).get("projects", []) if p["name"] == NAME]
if found:
    pid = found[0]["id"]; print("reusing existing Neon project", pid)
    br = next(b for b in call("GET", f"/projects/{pid}/branches")["branches"] if b.get("default"))["id"]
    role = call("GET", f"/projects/{pid}/branches/{br}/roles")["roles"][0]["name"]
    db = call("GET", f"/projects/{pid}/branches/{br}/databases")["databases"][0]["name"]
else:
    body = {"project": {"name": NAME, "region_id": "aws-us-east-2", "pg_version": 17}}
    r = requests.post(BASE + "/projects", headers=H, json=body, timeout=90)
    if r.status_code in (400, 403, 422) and "org" in r.text.lower():      # personal key on an org account: pass the org
        orgs = call("GET", "/users/me/organizations").get("organizations", [])
        body["project"]["org_id"] = orgs[0]["id"]
        r = requests.post(BASE + "/projects", headers=H, json=body, timeout=90)
    if r.status_code >= 300:
        sys.exit(f"Could not create the Neon project: {r.status_code} {r.text[:200]}")
    out = r.json(); pid = out["project"]["id"]; print("created Neon project", pid)
    role, db = out["roles"][0]["name"], out["databases"][0]["name"]

uri = call("GET", f"/projects/{pid}/connection_uri", params={"database_name": db, "role_name": role, "pooled": "true"})["uri"]
p = os.path.join(S.BASE, ".env"); lines = open(p, encoding="utf-8").read().splitlines()
lines = [l for l in lines if not l.startswith("DATABASE_URL=")] + [f"DATABASE_URL={uri}"]
open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("DATABASE_URL saved to .env (hidden)")

import psycopg2
with psycopg2.connect(uri, connect_timeout=30) as c, c.cursor() as cur:
    cur.execute("CREATE TABLE IF NOT EXISTS kv (kind text NOT NULL, key text NOT NULL, data jsonb NOT NULL, "
                "updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (kind, key))")
    cur.execute("SELECT count(*) FROM kv"); print("database reachable, table kv ready, rows:", cur.fetchone()[0])
