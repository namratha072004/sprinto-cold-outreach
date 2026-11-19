"""The new desk pages against REAL Postgres in a throwaway schema: access code, Drafts tabs, Rejected + overrule, Sources, Settings (rubric editor)."""
import os, sys, datetime as dt, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import dbfixture

SCHEMA = None
def setUpModule():
    global SCHEMA
    SCHEMA = dbfixture.create()
    import db, seed_rubric
    db.x("DELETE FROM rubric_versions")                           # replace the one-row stub with the REAL rubric so the editor has something to copy
    cur = db.conn().cursor(); seed_rubric.insert_rubric(cur, "seeded-test")
def tearDownModule():
    dbfixture.drop(SCHEMA)

class Base(unittest.TestCase):
    def setUp(self):
        import db, app
        self.db, self.app = db, app
        self.c = app.app.test_client()
        db.x("TRUNCATE runs, companies, contacts, emails, pain_hypotheses, evidence, research_rounds, qualification_scores, run_log, discovery_results, shortlist CASCADE")
        self.vid = db.one("SELECT id FROM rubric_versions WHERE is_active")["id"]
        self.rid = db.one("INSERT INTO runs (rubric_version_id) VALUES (%s) RETURNING id", (self.vid,))["id"]
        self.env = mock.patch.dict(os.environ, {"SENDER_NAME": "Tester"}); self.env.start()
        self.gm = mock.patch("review.gmail_drafts.create_or_replace", return_value=("d1", "replaced", "m1")); self.gmock = self.gm.start()
    def tearDown(self): self.gm.stop(); self.env.stop()
    def company(self, name, domain, status="DRAFT_READY", stage="done", assessed=False, score=80):
        j = self.db.J
        return self.db.one("INSERT INTO companies (run_id,domain,name,status,stage,total_score,gates,flags,sources_used,assessment,sources_text,finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'{}',%s,%s,%s,now()) RETURNING id",
                           (self.rid, domain, name, status, stage, score, j({"saas": {"state": "passed", "detail": "d"}}), j([]), j({"company_name": name}) if assessed else None, j({"u": "t"}) if assessed else None))["id"]
    def draft(self, cid, decision="pending", email="p@x.test", verification="valid", age_h=0):
        j = self.db.J
        self.db.x("INSERT INTO contacts (company_id,name,position,role_category,scores,total,passed,chosen,email,email_verification) VALUES (%s,'Pat Lee','CISO','security_leader_ciso',%s,9,true,true,%s,%s)",
                  (cid, j({"company_association": 2, "functional_ownership": 2, "ability_to_drive_action": 2, "match_to_need": 2, "identity_quality": 1}), email, verification))
        self.db.x("INSERT INTO emails (company_id,revision,subject,body,words,qa,qa_total,passed,is_current,original_subject,original_body,gmail_draft_id,gmail_message_id,review_decision,draft_created_at) "
                  "VALUES (%s,0,'Subj','Hi Pat,\n\nFirst real paragraph here.\n\nBye',8,%s,10,true,true,'Subj','Hi Pat,\n\nFirst real paragraph here.\n\nBye','d1','m1',%s, now() - make_interval(hours => %s))", (cid, j({}), decision, age_h))
    def post(self, path, body=None, **kw): return self.c.post(path, json=body or {}, **kw)

class TestOpenAccessAndPrivacy(Base):
    def test_every_action_works_without_any_code(self):
        cid = self.company("Acme", "acme.test"); self.draft(cid)
        self.assertTrue(self.c.get("/api/me").get_json()["can_edit"])
        for path in ("approve", "done", "contact-feedback"):
            self.assertEqual(self.post(f"/api/company/{cid}/{path}", {"correct": True}).status_code, 200, path)
        self.assertEqual(self.post(f"/api/company/{cid}/edit", {"subject": "S", "body": "New body text here."}).status_code, 200)
        self.assertEqual(self.post(f"/api/company/{cid}/reject").status_code, 200)
        self.assertEqual(self.post("/api/login", {"code": "x"}).status_code, 404)                               # the access-code feature is gone
    def test_prospect_addresses_are_masked_on_the_public_site_only(self):
        cid = self.company("Acme", "acme.test"); self.draft(cid)
        self.assertEqual(self.c.get(f"/api/company/{cid}").get_json()["contacts"][0]["email"], "p@x.test")      # local desk: full address
        with mock.patch.dict(os.environ, {"VERCEL": "1"}):
            self.assertEqual(self.c.get(f"/api/company/{cid}").get_json()["contacts"][0]["email"], "p***@x.test")

class TestDrafts(Base):
    def test_tabs_filters_and_counts(self):
        a = self.company("Verified Co", "a.test"); self.draft(a, "pending", "a@a.test", "valid")
        b = self.company("Unverified Co", "b.test"); self.draft(b, "edited", "b@b.test", "accept_all")
        n = self.company("No Address Co", "n.test"); self.draft(n, "pending", None, None)
        ap = self.company("Approved Co", "ap.test"); self.draft(ap, "approved")
        dn = self.company("Done Co", "dn.test"); self.draft(dn, "done")
        old = self.company("Old Co", "old.test"); self.draft(old, "pending", age_h=48)
        d = self.c.get("/api/drafts?tab=review").get_json()
        self.assertEqual(d["counts"], {"review": 4, "approved": 1, "done": 1, "older": 1})
        self.assertEqual(d["kinds"], {"verified": 2, "unverified": 1, "none": 1})
        self.assertEqual([i["name"] for i in self.c.get("/api/drafts?tab=review&kind=none").get_json()["items"]], ["No Address Co"])
        self.assertEqual([i["name"] for i in self.c.get("/api/drafts?tab=older").get_json()["items"]], ["Old Co"])
        self.assertEqual([i["name"] for i in self.c.get("/api/drafts?tab=done").get_json()["items"]], ["Done Co"])
        item = self.c.get("/api/drafts?tab=approved").get_json()["items"][0]
        self.assertEqual((item["snippet"], item["in_gmail"], item["gmail_open_url"]), ("First real paragraph here.", True, "https://mail.google.com/mail/u/0/#drafts?compose=m1"))
    def test_done_restore_and_overview(self):
        cid = self.company("Acme", "acme.test"); self.draft(cid, "approved")
        self.assertEqual(self.post(f"/api/company/{cid}/done").status_code, 200)
        self.assertEqual(self.db.one("SELECT review_decision FROM emails WHERE company_id=%s", (cid,))["review_decision"], "done")
        out = self.post(f"/api/company/{cid}/restore").get_json(); self.assertIn("still in Gmail", out["message"]); self.gmock.assert_called_once()
        self.gmock.return_value = ("d2", "created", "m2")
        self.assertIn("Recreated", self.post(f"/api/company/{cid}/restore").get_json()["message"])       # she had deleted it in Gmail
        ov = self.c.get("/api/overview").get_json(); self.assertEqual((ov["name"], ov["to_review"], ov["run"]["drafted"]), ("Tester", 0, 1))

class TestRejectedAndOverrule(Base):
    def test_rejected_list_reasons_and_filters(self):
        a = self.company("Weak Co", "w.test", "LOW_PRIORITY", assessed=True, score=50)
        self.db.x("UPDATE companies SET reason='Evidence is clear but the fit is weak: 50/100.' WHERE id=%s", (a,))
        b = self.company("NoSite Co", None, "INSUFFICIENT_EVIDENCE", assessed=False, score=None)
        self.company("Good Co", "g.test", "DRAFT_READY")
        d = self.c.get("/api/rejected").get_json()
        self.assertEqual(sorted(d["counts"]), ["INSUFFICIENT_EVIDENCE", "LOW_PRIORITY"])
        by = {i["name"]: i for i in d["items"]}
        self.assertEqual((by["Weak Co"]["overrule_allowed"], by["NoSite Co"]["overrule_allowed"]), (True, False))   # never researched: nothing to overrule
        self.assertEqual([i["name"] for i in self.c.get("/api/rejected?status=LOW_PRIORITY").get_json()["items"]], ["Weak Co"])
    def test_overrule_moves_it_on_once_and_records_her_note(self):
        a = self.company("Weak Co", "w.test", "LOW_PRIORITY", assessed=True)
        out = self.post(f"/api/company/{a}/overrule", {"note": "I know this founder"}); self.assertEqual(out.status_code, 200)
        co = self.db.one("SELECT * FROM companies WHERE id=%s", (a,))
        self.assertEqual((co["status"], co["stage"], co["overruled"], co["overrule_note"]), ("QUALIFIED", "finding_contact", True, "I know this founder"))
        self.assertIn("overruled_by_growth_lead", co["flags"])
        self.assertEqual(self.post(f"/api/company/{a}/overrule").status_code, 400)                        # not twice
        self.assertEqual(self.c.get("/api/rejected").get_json()["items"], [])                              # it left the Rejected page
        self.assertEqual(self.db.one("SELECT count(*) n FROM run_log WHERE event='overruled'")["n"], 1)
    def test_unresearched_company_cannot_be_overruled(self):
        b = self.company("NoSite Co", None, "INSUFFICIENT_EVIDENCE", assessed=False)
        self.assertEqual(self.post(f"/api/company/{b}/overrule").status_code, 400)

class TestSources(Base):
    def test_every_article_gets_a_plain_state(self):
        d = lambda h, name, outcome, cid=None: self.db.one("INSERT INTO discovery_results (run_id,query,headline,source_url,published_at,company_name,outcome,company_id) VALUES (%s,'q1',%s,%s,current_date,%s,%s,%s) RETURNING id",
                                                           (self.rid, h, "https://n/" + h, name, outcome, cid))["id"]
        drafted = self.company("A", "a.test", "DRAFT_READY"); rej = self.company("B", "b.test", "LOW_PRIORITY"); work = self.company("C", None, "QUEUED", "finding_website")
        d("a", "A", "shortlisted", drafted); d("b", "B", "shortlisted", rej); d("c", "C", "shortlisted", work); d("rounds", None, "not_funding_news"); d("old", "Old", "too_old"); d("never", "Z", None)
        out = self.c.get("/api/sources").get_json()
        self.assertEqual({k: v["count"] for k, v in out["outcomes"].items()}, {"drafted": 1, "rejected": 1, "researching": 1, "not_funding_news": 1, "too_old": 1, "not_read": 1})
        self.assertEqual(out["feeds"][0]["articles"], 6)
        self.assertEqual([i["headline"] for i in self.c.get("/api/sources?state=drafted").get_json()["items"]], ["a"])

class TestSettings(Base):
    def test_editor_reads_the_active_rubric(self):
        s = self.c.get("/api/settings").get_json()
        self.assertEqual((s["version"], sum(w["weight"] for w in s["weights"]), s["thresholds"]["total_min"]["value"], s["limits"]["shortlist_size"]["value"]), ("seeded-test", 100, 65, 15))
        self.assertEqual(len(s["capabilities"]), 4); self.assertEqual(len(s["queries"]), 4)
    def test_save_creates_a_new_active_version_and_never_touches_the_old_one(self):
        old_id = self.vid
        body = {"weights": {"business_fit": 30, "customer_trust": 20, "compliance_workload": 20, "reason_now": 20, "delivery_complexity": 10},
                "thresholds": {"total_min": 70}, "limits": {"hunter_calls_per_run": 8}, "signal_keywords": ["saas", "kubernetes"], "capabilities": ["evidence collection"],
                "queries": ["one search", "two search"], "shortlist_numbers": {"max_age_months": 6}}
        r = self.post("/api/settings", body); self.assertEqual(r.status_code, 200, r.get_json())
        new = self.db.one("SELECT * FROM rubric_versions WHERE is_active"); self.assertNotEqual(new["id"], old_id); self.assertIn("edited in Settings", new["source"])
        q = lambda sql, v: self.db.one(sql, (v,))
        self.assertEqual(q("SELECT count(*) n FROM rubric_versions WHERE is_active", 0)["n"], 1)
        self.assertEqual(q("SELECT weight FROM rubric_dimensions WHERE rubric_version_id=%s AND key='business_fit'", new["id"])["weight"], 30)
        self.assertEqual(q("SELECT weight FROM rubric_dimensions WHERE rubric_version_id=%s AND key='business_fit'", old_id)["weight"], 25)            # old version untouched
        self.assertEqual(float(q("SELECT value FROM rubric_thresholds WHERE rubric_version_id=%s AND key='total_min'", new["id"])["value"]), 70.0)
        self.assertEqual(q("SELECT params FROM rubric_gates WHERE rubric_version_id=%s AND key='score'", new["id"])["params"]["min"], 70)                  # the score gate follows
        self.assertEqual(float(q("SELECT value FROM rubric_thresholds WHERE rubric_version_id=%s AND key='total_min'", old_id)["value"]), 65.0)
        self.assertEqual(q("SELECT value FROM rubric_limits WHERE rubric_version_id=%s AND key='hunter_calls_per_run'", new["id"])["value"], 8)
        self.assertEqual([r["name"] for r in self.db.q("SELECT name FROM rubric_capabilities WHERE rubric_version_id=%s", (new["id"],))], ["evidence collection"])
        n_levels = lambda v: self.db.one("SELECT count(*) n FROM rubric_levels l JOIN rubric_dimensions d ON d.id=l.dimension_id WHERE d.rubric_version_id=%s", (v,))["n"]
        self.assertEqual((n_levels(new["id"]), n_levels(old_id)), (15, 15))                                                                                # level texts copied
        pol = q("SELECT value FROM rubric_rules WHERE rubric_version_id=%s AND area='shortlist' AND key='policy'", new["id"])["value"]
        self.assertEqual((pol["signal_keywords"], pol["max_age_months"], len(pol["outside_stage_patterns"])), (["saas", "kubernetes"], 6, 5))           # other policy parts kept
        self.assertEqual(self.db.one("SELECT count(*) n FROM rubric_rules WHERE rubric_version_id=%s", (new["id"],))["n"], self.db.one("SELECT count(*) n FROM rubric_rules WHERE rubric_version_id=%s", (old_id,))["n"])
    def test_the_role_rule_is_a_versioned_rubric_setting(self):
        r = self.post("/api/settings", {"rules": {"contact.only_preferred_roles": False}}); self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIs(self.db.one("select value from rubric_rules r join rubric_versions v on v.id=r.rubric_version_id where v.is_active and area='contact' and key='only_preferred_roles'")["value"], False)
        self.assertIs(self.c.get("/api/settings").get_json()["read_only"]["only_preferred_roles"], False)
        import rubric_db, rubric as R
        saved = R.ONLY_PREFERRED_ROLES
        try:
            rubric_db.apply(R, rubric_db.load()); self.assertIs(R.ONLY_PREFERRED_ROLES, False)             # the running code follows the saved rule
        finally: R.ONLY_PREFERRED_ROLES = saved
        self.assertEqual(self.post("/api/settings", {"rules": {"made.up": True}}).status_code, 400)
    def test_bad_input_is_refused_and_nothing_changes(self):
        for bad, why in [({"weights": {"business_fit": 50, "customer_trust": 20, "compliance_workload": 20, "reason_now": 20, "delivery_complexity": 10}}, "add up to 100"),
                         ({"thresholds": {"total_min": 150}}, "between"), ({"limits": {"hunter_calls_per_run": "lots"}}, "number"), ({"capabilities": [" "]}, "capability"),
                         ({"queries": []}, "Google News searches"), ({"thresholds": {"email_words_target_min": 200, "email_words_target_max": 100}}, "shortest")]:
            r = self.post("/api/settings", bad); self.assertEqual(r.status_code, 400, bad); self.assertIn(why, r.get_json()["error"])
        self.assertEqual(self.db.one("SELECT count(*) n FROM rubric_versions")["n"], 1)

if __name__ == "__main__":
    unittest.main()
