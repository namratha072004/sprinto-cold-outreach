"""Creates or REPLACES one draft per company in the Growth Lead's Gmail Drafts folder. This module cannot send mail:
it only calls drafts.create (POST /drafts) and drafts.update (PUT /drafts/{id}). OAuth refresh token only, no SMTP, no IMAP.
To = DEMO_EMAIL (prospect addresses are research data, never destinations). No Cc, no Bcc.
Errors are shown exactly as Google returns them. Authentication/permission problems stop this integration (rubric 11)."""
import base64, time
from email.message import EmailMessage
import requests
import settings as S

API = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
_TOKEN = {"value": None, "expires": 0}

class GmailSetupError(Exception):
    """Token, permission or setup problem: stop and tell the Growth Lead what to redo."""

class GmailError(Exception):
    """Anything else (network, quota)."""

def access_token():
    if _TOKEN["value"] and time.time() < _TOKEN["expires"] - 60:
        return _TOKEN["value"]
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": S.env("GMAIL_OAUTH_CLIENT_ID"), "client_secret": S.env("GMAIL_OAUTH_CLIENT_SECRET"),
        "refresh_token": S.env("GMAIL_OAUTH_REFRESH_TOKEN"), "grant_type": "refresh_token"})
    j = r.json()
    if not r.ok:
        raise GmailSetupError(f"Google rejected the Gmail token: {j.get('error')} ({j.get('error_description')}). Run scripts/gmail_authorize.py again.")
    _TOKEN.update(value=j["access_token"], expires=time.time() + int(j.get("expires_in", 3600)))
    return _TOKEN["value"]

def _raw(subject, body):
    to = S.env("DEMO_EMAIL")
    if not to:
        raise GmailSetupError("DEMO_EMAIL is not set.")
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject            # no Cc, no Bcc
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()

def _call(method, url, body):
    for attempt in range(1, 4):
        try:
            r = requests.request(method, url, headers={"Authorization": "Bearer " + access_token()}, json=body, timeout=30)
        except requests.RequestException as e:
            if attempt == 3:
                raise GmailError(f"Could not reach Gmail: {type(e).__name__}")
            time.sleep(2 ** attempt); continue
        if r.status_code in (429, 500, 502, 503) and attempt < 3:
            time.sleep(2 ** attempt); continue
        return r
    return r

def create_or_replace(subject, body, existing_id=None):
    """Returns (draft_id, 'created' | 'replaced', message_id). Replaces in place when a draft id is known, so editing never duplicates."""
    payload = {"message": {"raw": _raw(subject, body)}}
    if existing_id:
        r = _call("PUT", f"{API}/{existing_id}", payload)
        if r.status_code == 200:
            return r.json()["id"], "replaced", (r.json().get("message") or {}).get("id")
        if r.status_code != 404:                     # 404 = she deleted it in Gmail: make a fresh one below
            _fail(r)
    r = _call("POST", API, payload)
    if r.status_code != 200:
        _fail(r)
    return r.json()["id"], "created", (r.json().get("message") or {}).get("id")

def _fail(r):
    try:
        msg = r.json().get("error", {}).get("message", r.text[:200])
    except ValueError:
        msg = r.text[:200]
    text = f"Gmail answered HTTP {r.status_code}: {msg}"
    if r.status_code in (401, 403):
        raise GmailSetupError(text + " (check the Gmail API is enabled and the token has the gmail.compose permission)")
    raise GmailError(text)
