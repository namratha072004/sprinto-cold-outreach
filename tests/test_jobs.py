"""Shortlist rules, website verification and the job runner. Real Postgres in a throwaway schema; Gemini, web and Hunter are faked."""
import os, sys, datetime as dt, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbfixture
import shortlist, website_finder, jobs, research, outreach
import pipeline as P

POL = {"in_scope_stage_patterns": [r"series a\b", r"series b\b", r"series c\b"], "outside_stage_patterns": [r"\bpre-?seed\b", r"\bseed\b", r"series [d-z]\b", r"\bipo\b"],
       "stage_points": 3, "signal_points_each": 1, "signal_points_max": 3, "signal_keywords": ["saas", "security", "platform", "enterprise"], "max_age_months": 12}
CFG = {"rules": {("shortlist", "policy"): POL}, "limits": {"shortlist_size": 2}}

class TestShortlistRules(unittest.TestCase):
    def test_stage(self):
        self.assertEqual(shortlist.stage_points("Reco raises $30M Series B", POL), (3, True, None))
        self.assertEqual(shortlist.stage_points("Acme closes seed round", POL), (0, False, "seed"))
        self.assertEqual(shortlist.stage_points("Foo raises Series D", POL), (0, False, "series d"))
        self.assertTrue(shortlist.stage_points("Bar, after its seed, raises Series A", POL)[1])        # a stage inside A-C wins
        self.assertEqual(shortlist.stage_points("Baz raises money", POL), (0, False, None))          # unknown stage: not dropped
    def test_signals_are_capped_and_whole_words(self):
        found, pts = shortlist.signals("SaaS security platform for enterprise", POL); self.assertEqual((len(found), pts), (4, 3))
        self.assertEqual(shortlist.signals("datasaasify", POL), ([], 0))
    def test_same_company_names_match(self):
        self.assertEqual(shortlist.norm_name("Winn.ai"), shortlist.norm_name("WINN AI"))

class TestWebsiteFinder(unittest.TestCase):
    def test_domain_cleaning_blocks_news_and_social(self):
        self.assertEqual(website_finder.clean_domain("https://www.Acme.io/about"), "acme.io")
        for bad in ("techcrunch.com", "https://linkedin.com/company/x", "not a domain", None, "crunchbase.com"):
            self.assertIsNone(website_finder.clean_domain(bad), bad)
    def page(self, text, status="ok"): return {"status": status, "text": text, "url": "https://acme.io/", "retrieved_at": "2026-11-19"}
    def test_accepted_only_with_name_and_a_quote_really_on_the_page(self):
        text = "Acme builds security software for engineering teams. Trusted by many."
        ok = {"same_company": True, "excerpt": "Acme builds security software for engineering teams", "reason": "x"}
        with mock.patch.object(website_finder.scraper, "fetch_page", return_value=self.page(text)), mock.patch.object(P, "gemini_json", return_value=ok):
            self.assertTrue(website_finder.verify("Acme", "Acme raises Series A", "acme.io", {})["ok"])
    def test_invented_quote_is_rejected(self):
        text = "Acme builds security software."
        bad = {"same_company": True, "excerpt": "Acme is the world leader in quantum payroll", "reason": "x"}
        with mock.patch.object(website_finder.scraper, "fetch_page", return_value=self.page(text)), mock.patch.object(P, "gemini_json", return_value=bad):
            r = website_finder.verify("Acme", "h", "acme.io", {}); self.assertFalse(r["ok"]); self.assertIn("could not confirm", r["why"])
    def test_page_that_does_not_name_the_company_is_rejected_without_asking_the_ai(self):
        with mock.patch.object(website_finder.scraper, "fetch_page", return_value=self.page("A parked domain for sale.")), mock.patch.object(P, "gemini_json") as g:
            self.assertFalse(website_finder.verify("Acme", "h", "acme.io", {})["ok"]); g.assert_not_called()
    def test_unreadable_homepage_is_a_failure_not_a_guess(self):
        with mock.patch.object(website_finder.scraper, "fetch_page", return_value=self.page("", "blocked")):
            self.assertIn("could not be read", website_finder.verify("Acme", "h", "acme.io", {})["why"])
    def test_no_suggestion_means_unknown(self):
        with mock.patch.object(P, "gemini_json", return_value={"domain": None}):
            self.assertFalse(website_finder.find("Acme", "h", {})["ok"])

class TestDb(unittest.TestCase):
    schema = None
    @classmethod
    def setUpClass(cls): cls.schema = dbfixture.create()
    @classmethod
    def tearDownClass(cls): dbfixture.drop(cls.schema)
    def setUp(self):
        import db
        self.db = db
        db.x("TRUNCATE runs, companies, evidence, discovery_results, shortlist, run_log, research_rounds, qualification_scores, contacts, emails CASCADE")
        self.rid = db.one("INSERT INTO runs (rubric_version_id) VALUES (1) RETURNING id")["id"]
        self.p = mock.patch.object(research, "ensure_rubric", return_value=CFG); self.p.start()
    def tearDown(self): self.p.stop()
    def add(self, headline, name, day=0, source="Pub"):
        return self.db.one("INSERT INTO discovery_results (run_id,query,headline,source_name,source_url,published_at,company_name) VALUES (%s,'q',%s,%s,%s,%s,%s) RETURNING id",
                           (self.rid, headline, source, "https://n/" + headline[:20] + str(day), dt.date.today() - dt.timedelta(days=day), name))["id"]
    def outcome(self, i): return self.db.one("SELECT outcome FROM discovery_results WHERE id=%s", (i,))["outcome"]

    def test_shortlist_sorts_every_article_into_an_outcome_and_keeps_top_n(self):
        a = self.add("Reco raises Series B for SaaS security platform", "Reco")
        a2 = self.add("Reco raises $55M as security startups crowd the market", "Reco", 5)
        b = self.add("Coram AI raises Series A for enterprise security", "Coram AI", 2)
        c = self.add("Zed raises Series A", "Zed", 1)
        roundup = self.add("The 24 Largest Funding Rounds of June", None)
        seed = self.add("Tiny closes seed round for SaaS", "Tiny")
        old = self.add("Oldco raises Series A", "Oldco", 500)
        out = shortlist.build(self.rid)
        self.assertEqual(out["kept"], 2)                                                  # size from Neon (2 in this test)
        self.assertEqual([self.outcome(x) for x in (a, a2, b, c, roundup, seed, old)],
                         ["shortlisted", "merged", "shortlisted", "not_shortlisted", "not_funding_news", "outside_stage", "too_old"])
        names = [r["name"] for r in self.db.q("SELECT name FROM companies WHERE run_id=%s ORDER BY shortlist_rank", (self.rid,))]
        self.assertEqual(names, ["Reco", "Coram AI"])
        co = self.db.one("SELECT * FROM companies WHERE name='Reco'")
        self.assertEqual((co["status"], co["stage"], co["domain"], co["shortlist_rank"]), ("QUEUED", "finding_website", None, 1))
        self.assertEqual(self.db.one("SELECT merged_into FROM discovery_results WHERE id=%s", (a2,))["merged_into"], a)
        self.assertEqual(self.db.one("SELECT count(*) n FROM shortlist")["n"], 2)

    def queued(self, name="Acme"):
        d = self.add(f"{name} raises Series A", name)
        cid = self.db.one("INSERT INTO companies (run_id,name,status,stage) VALUES (%s,%s,'QUEUED','finding_website') RETURNING id", (self.rid, name))["id"]
        self.db.x("UPDATE discovery_results SET company_id=%s WHERE id=%s", (cid, d)); return cid, d

    def test_website_found_moves_company_forward_and_saves_the_proof(self):
        cid, d = self.queued()
        ok = {"ok": True, "domain": "acme.io", "url": "https://acme.io/", "excerpt": "Acme builds security software", "retrieved_on": "2026-11-19"}
        with mock.patch.object(website_finder, "find", return_value=ok):
            r = jobs.advance(cid)
        self.assertEqual((r["stage"], r["step"]), ("reading_website", "website"))
        self.assertEqual(self.db.one("SELECT domain FROM companies WHERE id=%s", (cid,))["domain"], "acme.io")
        ev = self.db.one("SELECT * FROM evidence WHERE company_id=%s", (cid,)); self.assertEqual((ev["dimension"], ev["label"], ev["excerpt"]), ("website", "observed", "Acme builds security software"))
        dr = self.db.one("SELECT domain_status, company_domain FROM discovery_results WHERE id=%s", (d,)); self.assertEqual((dr["domain_status"], dr["company_domain"]), ("confirmed", "acme.io"))
    def test_website_not_confirmed_is_insufficient_evidence_with_a_clear_reason(self):
        cid, d = self.queued()
        with mock.patch.object(website_finder, "find", return_value={"ok": False, "why": "the homepage of x.io does not name 'Acme'"}):
            r = jobs.advance(cid)
        co = self.db.one("SELECT * FROM companies WHERE id=%s", (cid,))
        self.assertEqual((r["stage"], co["status"], "website_unconfirmed" in co["flags"]), ("done", "INSUFFICIENT_EVIDENCE", True))
        self.assertIn("never guessed", co["escalation_reason"]); self.assertEqual(self.db.one("SELECT outcome FROM discovery_results WHERE id=%s", (d,))["outcome"], "no_website")
    def test_steps_run_in_order(self):
        cid, _ = self.queued()
        self.db.x("UPDATE companies SET stage='reading_website', domain='acme.io' WHERE id=%s", (cid,))
        with mock.patch.object(research, "research_company") as rc, mock.patch.object(outreach, "finish_company") as fc:
            jobs.advance(cid); rc.assert_called_once_with(self.rid, "acme.io", cid=cid); fc.assert_not_called()
            self.db.x("UPDATE companies SET stage='finding_contact', claimed_at=NULL WHERE id=%s", (cid,))
            jobs.advance(cid); fc.assert_called_once_with(self.rid, "acme.io")
    def test_two_workers_cannot_run_the_same_step(self):
        cid, _ = self.queued()
        self.db.x("UPDATE companies SET claimed_at=now() WHERE id=%s", (cid,))
        with mock.patch.object(website_finder, "find") as f:
            r = jobs.advance(cid)
        f.assert_not_called(); self.assertTrue(r.get("busy"))
    def test_a_failing_step_marks_error_and_does_not_raise(self):
        cid, _ = self.queued()
        with mock.patch.object(website_finder, "find", side_effect=RuntimeError("model exploded")):
            r = jobs.advance(cid)
        self.assertEqual((r["status"], r["stage"]), ("ERROR", "done"))

if __name__ == "__main__":
    unittest.main()
