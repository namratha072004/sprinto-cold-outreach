"""Review desk tests against REAL Postgres (Neon) in a throwaway schema that is dropped afterwards. Real data is never touched.
They prove: approve/edit/reject only record decisions; an edit replaces the existing Gmail draft; the app has no send route."""
import os, sys, uuid, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import settings as S

SCHEMA = "t_" + uuid.uuid4().hex[:10]
HAVE_DB = bool(S.env("DATABASE_URL"))

def setUpModule():
    if not HAVE_DB:
        raise unittest.SkipTest("DATABASE_URL not set")
    import psycopg2
    with psycopg2.connect(S.env("DATABASE_URL"), connect_timeout=30) as c:
        c.autocommit = True
        c.cursor().execute(f"CREATE SCHEMA {SCHEMA}")
    os.environ["SPRINTO_DB_SCHEMA"] = SCHEMA
    import db
    db._C["conn"] = None
    db.conn().cursor().execute(open(os.path.join(S.BASE, "schema.sql"), encoding="utf-8").read())
    db.x("INSERT INTO rubric_versions (id, version, source, is_active) VALUES (1, 'test', 'test', true)")

def tearDownModule():
    if not HAVE_DB:
        return
    import db, psycopg2
    if db._C["conn"] is not None:
        db._C["conn"].close()                                  # closes the session that carried the SET search_path
    db._C["conn"] = None
    os.environ.pop("SPRINTO_DB_SCHEMA", None)
    with psycopg2.connect(S.env("DATABASE_URL"), connect_timeout=30) as c:
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA {SCHEMA} CASCADE")

class Desk(unittest.TestCase):
    def setUp(self):
        import db, app
        self.db, self.client = db, app.app.test_client()
        db.x("TRUNCATE runs, companies, contacts, emails, pain_hypotheses, evidence, research_rounds, qualification_scores, run_log, discovery_results CASCADE")
        rid = db.one("INSERT INTO runs (rubric_version_id) VALUES (1) RETURNING id")["id"]
        self.cid = self.company(rid, "acme.test", "DRAFT_READY", "done")
        db.x("INSERT INTO contacts (company_id,name,position,role_category,scores,total,passed,chosen,email) VALUES (%s,'Priya Rao','Head of Security','security_leader_ciso',%s,9,true,true,'p@acme.test')",
             (self.cid, db.J({"company_association": 2, "functional_ownership": 2, "ability_to_drive_action": 2, "match_to_need": 2, "identity_quality": 1})))
        self.eid = db.one("INSERT INTO emails (company_id,revision,subject,body,words,qa,qa_total,passed,is_current,original_subject,original_body,gmail_draft_id) "
                          "VALUES (%s,0,'Subject A','Hi Priya, original body. Is that useful?',7,%s,10,true,true,'Subject A','Hi Priya, original body. Is that useful?','draft-1') RETURNING id",
                          (self.cid, db.J({"scores": {}, "mandatory": {}, "code_problems": [], "feedback": ""})))["id"]
        self.gm = mock.patch("review.gmail_drafts.create_or_replace", return_value=("draft-1", "replaced")); self.gmock = self.gm.start()
    def tearDown(self): self.gm.stop()
    def company(self, rid, domain, status, stage, age=None):
        return self.db.one("INSERT INTO companies (run_id,domain,name,status,stage,total_score,gates,flags,sources_used) VALUES (%s,%s,'Acme',%s,%s,80,%s,'{}',%s) RETURNING id",
                           (rid, domain, status, stage, self.db.J({"saas": {"state": "passed", "detail": "d"}}), self.db.J([])))["id"]
    def post(self, path, body=None): return self.client.post(f"/api/company/{self.cid}/{path}", json=body or {})
    def decision(self): return self.db.one("SELECT * FROM emails WHERE id=%s", (self.eid,))

    def test_list_and_detail_show_real_rows(self):
        row = self.client.get("/api/companies").get_json()[0]
        self.assertEqual((row["status"], row["needs_review"], row["score"]), ("Draft ready for your review", True, 80))
        d = self.client.get(f"/api/company/{self.cid}").get_json()
        self.assertEqual((d["email"]["subject"], d["contacts"][0]["name"], d["email"]["gmail_draft_id"]), ("Subject A", "Priya Rao", "draft-1"))
        self.assertIsNotNone(self.decision()["opened_at"])                      # the review clock started
    def test_approve_only_records_and_never_touches_gmail(self):
        self.client.get(f"/api/company/{self.cid}")
        out = self.post("approve").get_json()
        e = self.decision()
        self.assertEqual(e["review_decision"], "approved"); self.assertIsNotNone(e["reviewed_at"]); self.assertIsNotNone(e["review_seconds"])
        self.assertEqual(out["view"]["label"], "Approved by you"); self.gmock.assert_not_called()
    def test_reject_records_reason_and_does_not_touch_gmail(self):
        self.post("reject", {"reason": "too generic"})
        e = self.decision(); self.assertEqual((e["review_decision"], e["review_note"]), ("rejected", "too generic")); self.gmock.assert_not_called()
    def test_edit_replaces_the_same_gmail_draft(self):
        out = self.post("edit", {"subject": "Subject B", "body": "Hi Priya, my own words. Okay?"}).get_json()
        e = self.decision()
        self.assertEqual((e["subject"], e["review_decision"], e["original_subject"]), ("Subject B", "edited", "Subject A"))   # original kept for the rewrite metric
        self.gmock.assert_called_once_with("Subject B", "Hi Priya, my own words. Okay?", "draft-1")                           # same draft id: replace, never duplicate
        self.assertIsNone(out["gmail_error"])
    def test_edit_when_gmail_fails_keeps_text_and_reports_exact_error(self):
        import gmail_drafts
        self.gmock.side_effect = gmail_drafts.GmailSetupError("Gmail answered HTTP 403: something exact")
        out = self.post("edit", {"subject": "S2", "body": "B2 body"}).get_json()
        e = self.decision()
        self.assertEqual(e["subject"], "S2"); self.assertIn("HTTP 403: something exact", out["gmail_error"]); self.assertIn("HTTP 403", e["gmail_draft_error"])
    def test_empty_edit_is_refused(self):
        self.assertEqual(self.post("edit", {"subject": " ", "body": "x"}).status_code, 400)
    def test_contact_feedback_and_scorecard(self):
        self.post("contact-feedback", {"correct": True})
        self.post("edit", {"subject": "S3", "body": "Totally different text here."})
        m = self.client.get("/api/metrics").get_json()
        self.assertEqual((m["contact_accuracy_pct"], m["rewrite_rate_pct"], m["drafts_reviewed"]), (100.0, 100.0, 1))
        self.assertFalse(m["kill_checks"]["enough_data"]); self.assertIsNone(m["kill_checks"]["rewrites_over_50pct"])   # one draft is not enough to judge
    def test_approved_unedited_draft_is_not_a_rewrite(self):
        self.post("approve")
        self.assertEqual(self.client.get("/api/metrics").get_json()["rewrite_rate_pct"], 0.0)
    def test_cut_off_run_is_labelled(self):
        rid = self.db.one("SELECT run_id FROM companies WHERE id=%s", (self.cid,))["run_id"]
        cid2 = self.company(rid, "slow.test", "WORKING", "reading_website")
        self.db.x("UPDATE companies SET updated_at = now() - interval '20 minutes' WHERE id=%s", (cid2,))
        rows = {r["domain"]: r for r in self.client.get("/api/companies").get_json()}
        self.assertEqual(rows["slow.test"]["key"], "STALE")
    def test_post_without_json_is_refused(self):
        self.assertEqual(self.client.post(f"/api/company/{self.cid}/approve", data="x").status_code, 415)
    def test_the_app_has_no_send_route(self):
        import app
        paths = [str(r) for r in app.app.url_map.iter_rules()]
        self.assertFalse([p for p in paths if "send" in p.lower()], paths)

if __name__ == "__main__":
    unittest.main()
