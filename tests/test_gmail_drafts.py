"""Gmail draft writer: replace-not-duplicate, exact errors, To = DEMO_EMAIL only, and NO way to send."""
import os, sys, re, base64, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gmail_drafts as G

class Resp:
    def __init__(self, code, body=None): self.status_code, self._b = code, body or {}; self.text = str(body); self.ok = code < 400
    def json(self): return self._b

class TestDrafts(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"DEMO_EMAIL": "demo@example.test", "GMAIL_OAUTH_CLIENT_ID": "c", "GMAIL_OAUTH_CLIENT_SECRET": "s", "GMAIL_OAUTH_REFRESH_TOKEN": "r"})
        self.env.start(); G._TOKEN.update(value="tok", expires=9e12)
        self.sleep = mock.patch.object(G.time, "sleep"); self.sleep.start()
    def tearDown(self): self.env.stop(); self.sleep.stop()
    def calls(self, *responses):
        return mock.patch.object(G.requests, "request", side_effect=list(responses))
    def test_creates_when_no_draft_exists(self):
        with self.calls(Resp(200, {"id": "d1"})) as r:
            self.assertEqual(G.create_or_replace("S", "B"), ("d1", "created"))
        self.assertEqual(r.call_args[0][0], "POST")
    def test_edits_replace_the_same_draft(self):
        with self.calls(Resp(200, {"id": "d1"})) as r:
            self.assertEqual(G.create_or_replace("S2", "B2", "d1"), ("d1", "replaced"))
        self.assertEqual((r.call_args[0][0], r.call_args[0][1].rsplit("/", 1)[1]), ("PUT", "d1"))
    def test_draft_deleted_in_gmail_is_recreated_once(self):
        with self.calls(Resp(404, {"error": {"message": "not found"}}), Resp(200, {"id": "d2"})):
            self.assertEqual(G.create_or_replace("S", "B", "gone"), ("d2", "created"))
    def test_to_is_demo_email_only_no_cc_bcc(self):
        with self.calls(Resp(200, {"id": "d1"})) as r:
            G.create_or_replace("Subject here", "Body here")
        import email
        msg = email.message_from_bytes(base64.urlsafe_b64decode(r.call_args[1]["json"]["message"]["raw"]))
        self.assertEqual((msg["To"], msg["Subject"], msg["Cc"], msg["Bcc"]), ("demo@example.test", "Subject here", None, None))
    def test_permission_error_is_shown_exactly_and_marked_setup(self):
        with self.calls(Resp(403, {"error": {"message": "Gmail API has not been used in project 123"}})):
            with self.assertRaises(G.GmailSetupError) as e: G.create_or_replace("S", "B")
        self.assertIn("HTTP 403: Gmail API has not been used in project 123", str(e.exception))
    def test_expired_token_surfaces_google_error(self):
        G._TOKEN.update(value=None, expires=0)
        with mock.patch.object(G.requests, "post", return_value=Resp(400, {"error": "invalid_grant", "error_description": "Bad Request"})):
            with self.assertRaises(G.GmailSetupError) as e: G.access_token()
        self.assertIn("invalid_grant", str(e.exception)); self.assertIn("gmail_authorize.py", str(e.exception))
    def test_transient_error_retried_then_works(self):
        with self.calls(Resp(503), Resp(200, {"id": "d1"})):
            self.assertEqual(G.create_or_replace("S", "B")[0], "d1")
    def test_missing_demo_email_is_a_setup_error(self):
        with mock.patch.dict(os.environ, {"DEMO_EMAIL": ""}), mock.patch.object(G.S, "_ENV", {}):
            with self.assertRaises(G.GmailSetupError): G.create_or_replace("S", "B")

class TestNoSendPath(unittest.TestCase):
    """The brief: the software must have no code path that sends mail. Scans EVERY project .py file (not tests)."""
    FORBIDDEN = [r"smtplib", r"SMTP", r"sendmail", r"send_message", r"drafts/send", r"messages/send", r"\.send\(", r"imaplib", r"gmail\.send", r"mail\.google\.com/[\"']"]
    def project_files(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = [f for f in os.listdir(base) if f.endswith(".py")]
        out += ["scripts/" + f for f in os.listdir(os.path.join(base, "scripts")) if f.endswith(".py")]
        return base, out
    def test_no_sending_code_anywhere(self):
        base, files = self.project_files()
        self.assertGreater(len(files), 15)
        for f in files:
            src = re.sub(r'''""".*?"""''', "", open(os.path.join(base, f), encoding="utf-8").read(), flags=re.S)   # ignore docstrings
            src = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
            for pat in self.FORBIDDEN:
                self.assertIsNone(re.search(pat, src), f"{f} contains '{pat}'")
    def test_only_draft_endpoints_are_used(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(base, "gmail_drafts.py"), encoding="utf-8").read()
        self.assertEqual(sorted(set(re.findall(r'"(POST|PUT|GET|DELETE|PATCH)"', src))), ["POST", "PUT"])
        self.assertNotIn("/send", src)

if __name__ == "__main__":
    unittest.main()
