"""Creates ONE clearly labelled TEST draft in the Growth Lead's Gmail Drafts folder, so we can prove the connection works.
It only calls drafts.list / drafts.create / drafts.update. There is no send call anywhere in this project.
If a test draft already exists it is replaced, not duplicated. Any error is printed exactly as Google returned it.
Run:  .venv/bin/python scripts/gmail_test_draft.py"""
import os, sys, base64, json
from email.message import EmailMessage
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import settings as S

r = requests.post("https://oauth2.googleapis.com/token", data={
    "client_id": S.env("GMAIL_OAUTH_CLIENT_ID"), "client_secret": S.env("GMAIL_OAUTH_CLIENT_SECRET"),
    "refresh_token": S.env("GMAIL_OAUTH_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=30)
j = r.json()
if not r.ok:
    sys.exit(f"TOKEN ERROR (HTTP {r.status_code}): {json.dumps({k: j.get(k) for k in ('error', 'error_description')})}")
print("scope granted:", j.get("scope"))
H = {"Authorization": "Bearer " + j["access_token"]}
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SUBJECT = "[TEST] Sprinto outreach: Gmail draft connection test (not a real email)"
m = EmailMessage()
m["To"] = S.env("DEMO_EMAIL"); m["Subject"] = SUBJECT                       # no Cc, no Bcc
m.set_content("This is a TEST draft created by the Sprinto outreach software to prove it can write to Gmail Drafts.\n"
              "It was never sent and will not be sent by the software. You can delete it.")
body = {"message": {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}}
existing = requests.get(f"{API}/drafts", headers=H, params={"q": f'subject:"{SUBJECT}"'}, timeout=30)
if not existing.ok:
    sys.exit(f"GMAIL ERROR on drafts.list (HTTP {existing.status_code}): {existing.text[:400]}")
found = [d["id"] for d in existing.json().get("drafts", [])]
if found:
    out = requests.put(f"{API}/drafts/{found[0]}", headers=H, json=body, timeout=30); verb = "replaced existing test draft"
else:
    out = requests.post(f"{API}/drafts", headers=H, json=body, timeout=30); verb = "created test draft"
if not out.ok:
    sys.exit(f"GMAIL ERROR on draft write (HTTP {out.status_code}): {out.text[:400]}")
print(f"OK: {verb}. draft id: {out.json()['id']} | labels: {out.json()['message'].get('labelIds')} | To: {S.env('DEMO_EMAIL')} | nothing was sent")
