"""One-time (and weekly, while the Google app is in Testing mode) Gmail sign-in for the Growth Lead's account.
Asks Google for ONE permission, gmail.compose ("manage drafts and send emails"). The software only ever calls drafts.create/update/get/list;
it contains no send code. Saves the new refresh token into .env (never printed).
Run:  .venv/bin/python scripts/gmail_authorize.py [--incognito | --no-browser]     -> a browser window opens (--incognito = private Chrome window; --no-browser = just print the link); sign in as the Growth Lead and press Allow.
Needs GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET in .env, and http://localhost:8085/ listed as an authorised redirect URI
(a 'Desktop app' client allows it automatically)."""
import os, sys, secrets, threading, webbrowser, urllib.parse, http.server
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import settings as S

PORT, SCOPE = 8085, "https://www.googleapis.com/auth/gmail.compose"
REDIRECT = f"http://localhost:{PORT}/"
cid, secret = S.env("GMAIL_OAUTH_CLIENT_ID"), S.env("GMAIL_OAUTH_CLIENT_SECRET")
if not cid or not secret:
    sys.exit("GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET are missing in .env")
state, got = secrets.token_urlsafe(16), {}

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] == state and "code" in q:
            got["code"] = q["code"][0]; msg = "Done. You can close this tab and go back to the terminal."
        else:
            got["error"] = q.get("error", ["unknown"])[0]; msg = "Sign-in did not complete: " + got["error"]
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(msg.encode())
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("localhost", PORT), H)
url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
    "access_type": "offline", "prompt": "select_account consent", "state": state,
    "login_hint": S.env("GROWTH_LEAD_GMAIL") or S.env("GMAIL_ADDRESS") or ""})
print(f"Redirect address this script uses (must be listed in Google Cloud): {REDIRECT}")
print("Opening the Google sign-in page. Pick the Growth Lead's PERSONAL Gmail on the account chooser.")
print("If it does not open, copy this link into a browser:\n" + url)
if "--incognito" in sys.argv:        # private Chrome window: your school account is not signed in there, so you sign in with the personal one
    import subprocess
    subprocess.run(["open", "-na", "Google Chrome", "--args", "--incognito", url])
elif "--no-browser" not in sys.argv:
    webbrowser.open(url)
srv.timeout = 300
while not got:
    srv.handle_request()
if "code" not in got:
    sys.exit("Google returned: " + got.get("error", "nothing"))
r = requests.post("https://oauth2.googleapis.com/token", data={"code": got["code"], "client_id": cid, "client_secret": secret,
                  "redirect_uri": REDIRECT, "grant_type": "authorization_code"}, timeout=30)
j = r.json()
if not r.ok or "refresh_token" not in j:
    sys.exit("Token exchange failed: " + str({k: j.get(k) for k in ("error", "error_description")}))
p = os.path.join(S.BASE, ".env"); lines = [l for l in open(p, encoding="utf-8").read().splitlines() if not l.startswith("GMAIL_OAUTH_REFRESH_TOKEN=")]
open(p, "w", encoding="utf-8").write("\n".join(lines + [f"GMAIL_OAUTH_REFRESH_TOKEN={j['refresh_token']}"]) + "\n")
prof = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers={"Authorization": "Bearer " + j["access_token"]}, timeout=30)
print("New refresh token saved to .env (hidden). Scope granted:", j.get("scope"), "| mailbox:", prof.json().get("emailAddress") if prof.ok else prof.text[:120])
