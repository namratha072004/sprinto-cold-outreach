"""Offline tests for the scraper rules (rubric sections 2, 4, 11 + brief). No network, no database."""
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rubric as R, scraper, discovery, rubric_db

class Resp:
    def __init__(self, code, text="<html><title>T</title><body><p>hello world page</p></body></html>", ctype="text/html"):
        self.status_code, self.text, self.headers, self.url = code, text, {"content-type": ctype}, "https://x.test/"

class TestRetryRules(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(scraper, "throttle"), mock.patch.object(scraper.time, "sleep")]
        for p in self.p: p.start()
        R.MAX_READ_ATTEMPTS = 3
    def tearDown(self):
        for p in self.p: p.stop()
    def run_get(self, *responses):
        with mock.patch.object(scraper.requests, "get", side_effect=list(responses)) as g:
            out = scraper.get("https://x.test/a")
        return out, g.call_count
    def test_blocked_is_not_retried(self):
        (label, r, att), calls = self.run_get(Resp(403)); self.assertEqual((label, att, calls), ("blocked", 1, 1))
    def test_not_found_is_not_retried(self):
        (label, r, att), calls = self.run_get(Resp(404)); self.assertEqual((label, calls), ("not_found", 1))
    def test_server_errors_retry_then_stop_at_three(self):
        (label, r, att), calls = self.run_get(Resp(503), Resp(503), Resp(503)); self.assertEqual((label, att, calls), ("failed", 3, 3))
    def test_transient_then_success(self):
        (label, r, att), calls = self.run_get(Resp(500), Resp(200)); self.assertEqual((label, att), ("ok", 2))
    def test_attempt_limit_comes_from_the_rubric_value(self):
        R.MAX_READ_ATTEMPTS = 2
        (label, r, att), calls = self.run_get(Resp(500), Resp(500), Resp(500)); self.assertEqual((label, calls), ("failed", 2))
    def test_pdf_is_not_read(self):
        (label, r, att), calls = self.run_get(Resp(200, ctype="application/pdf")); self.assertEqual(label, "failed")

class TestRobots(unittest.TestCase):
    def check(self, robots_status, robots_text, url):
        with mock.patch.object(scraper, "cache_get", return_value={"status": robots_status, "text": robots_text}):
            return scraper.robots_allows(url)[0]
    def test_disallowed_path_is_refused(self):
        self.assertFalse(self.check("ok", "User-agent: *\nDisallow: /private", "https://x.test/private/page"))
    def test_allowed_path_is_read(self):
        self.assertTrue(self.check("ok", "User-agent: *\nDisallow: /private", "https://x.test/security"))
    def test_our_bot_name_can_be_singled_out(self):
        self.assertFalse(self.check("ok", "User-agent: SprintoResearchBot\nDisallow: /", "https://x.test/security"))
    def test_missing_robots_means_allowed(self):
        self.assertTrue(self.check("not_found", "", "https://x.test/security"))
    def test_unreadable_robots_is_not_assumed_open(self):
        self.assertFalse(self.check("failed", "", "https://x.test/security"))
    def test_disallowed_page_is_a_retrieval_failure_not_poor_fit(self):
        with mock.patch.object(scraper, "cache_get", return_value=None), mock.patch.object(scraper, "robots_allows", return_value=(False, "robots.txt disallows this page")), \
             mock.patch.object(scraper, "cache_put"):
            self.assertEqual(scraper.fetch_page("https://x.test/security")["status"], "robots_disallowed")
    def test_linkedin_and_login_never_fetched(self):
        with mock.patch.object(scraper, "cache_get", return_value=None), mock.patch.object(scraper, "get") as g:
            self.assertEqual(scraper.fetch_page("https://www.linkedin.com/company/x")["status"], "failed")
            self.assertEqual(scraper.fetch_page("https://x.test/login")["status"], "failed")
            g.assert_not_called()

class TestCache(unittest.TestCase):
    def test_cached_page_is_not_refetched(self):
        import datetime as dt
        row = {"status": "ok", "text": "cached text", "links": [], "attempts": 1, "retrieved_at": dt.datetime.now(dt.timezone.utc)}
        with mock.patch.object(scraper, "cache_get", return_value=row), mock.patch.object(scraper, "get") as g:
            p = scraper.fetch_page("https://x.test/a")
        g.assert_not_called(); self.assertTrue(p["from_cache"])

class TestNews(unittest.TestCase):
    RSS = ('<rss><channel><item><title>Acme raises $20M Series B - TechNews</title><link>https://news.google.com/rss/articles/abc</link>'
           '<pubDate>Wed, 18 Nov 2026 08:00:00 GMT</pubDate><source url="https://technews.example">TechNews</source>'
           '<description>&lt;a href="x"&gt;Acme raises&lt;/a&gt;</description></item><item><title></title><link></link></item></channel></rss>')
    def test_parse(self):
        items = scraper.parse_rss(self.RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["published"], items[0]["publisher"], items[0]["publisher_url"]), ("2026-11-18", "TechNews", "https://technews.example"))
    def test_bad_xml_gives_nothing(self):
        self.assertEqual(scraper.parse_rss("<not xml"), [])

class TestDiscovery(unittest.TestCase):
    def test_domain_never_guessed(self):
        it = {"headline": "Acme raises $20M Series B", "description": "", "publisher_url": "https://technews.example"}
        self.assertIsNone(discovery.confirmed_domain(it))
    def test_domain_confirmed_only_if_written_in_source_and_not_publisher(self):
        self.assertEqual(discovery.confirmed_domain({"headline": "Acme (acme.io) raises Series A", "description": "", "publisher_url": "https://technews.example"}), "acme.io")
        self.assertIsNone(discovery.confirmed_domain({"headline": "Via technews.example: Acme raises", "description": "", "publisher_url": "https://technews.example"}))
    def test_company_name_must_be_in_headline(self):
        self.assertTrue(discovery.name_in_headline("Acme", "Acme raises $20M Series B"))
        self.assertFalse(discovery.name_in_headline("Globex", "Acme raises $20M Series B"))
    def test_queries_come_from_neon_rules_and_use_rubric_window(self):
        cfg = {"thresholds": {"reason_now_level1_months": 12.0}, "rules": {("search", "query_templates_PROPOSED"): ["SaaS raises {year}"]}}
        qs = discovery.queries(cfg)
        self.assertTrue(qs[0].endswith("when:1y")); self.assertNotIn("{year}", qs[0])

class TestRubricFromNeon(unittest.TestCase):
    def test_changing_a_neon_value_changes_the_code_values(self):
        cfg = {"thresholds": {"total_min": 70.0, "contact_pass_total": 9.0, "pain_pass_total": 7.0, "pain_min_evidence_score": 2.0, "email_pass_total": 8.0,
                              "email_words_tolerance_min": 60.0, "email_words_tolerance_max": 140.0, "reason_now_level1_months": 9.0, "reason_now_level2_months": 3.0},
               "limits": {"initial_extra_pages": 2, "max_extra_rounds": 1, "pages_per_round": 1, "max_read_attempts": 2, "max_chars_per_source": 5000, "email_max_revisions": 1},
               "gates": {"saas": {"params": {"min": 2}}, "compliance": {"params": {"min": 1}}, "series": {"params": {"stages": ["series a", "series b"]}}},
               "weights": {"business_fit": 25}, "dimensions": [{"key": "business_fit", "weight": 25}], "levels": {"business_fit": {0: "none", 1: "some", 2: "full"}},
               "rules": {("contact", "role_preference"): ["security_leader_ciso"], ("email", "mandatory_checks"): ["a_check"], ("email", "banned_patterns"): [r"\bfoo"]}, "capabilities": ["evidence collection"], "version": "t"}
        saved = {k: getattr(R, k) for k in dir(R) if k.isupper()}
        try:
            rubric_db.apply(R, cfg)
            self.assertEqual((R.THRESHOLD_TOTAL, R.MAX_ROUNDS, R.MAX_READ_ATTEMPTS, R.ALLOWED_STAGES), (70.0, 1, 2, {"series a", "series b"}))
            self.assertEqual((R.REASON_NOW_L1_MONTHS, R.REASON_NOW_L2_MONTHS), (9, 3))
            self.assertIn("business_fit (25): 0=none; 1=some; 2=full", R.LEVELS)
            self.assertEqual(R.ROLE_PREFERENCE, ["security_leader_ciso", "other"])
            self.assertEqual((R.MANDATORY_CHECKS, R.BANNED_PATTERNS), (["a_check"], [r"\bfoo"]))
        finally:
            for k, v in saved.items(): setattr(R, k, v)

if __name__ == "__main__":
    unittest.main()


class TestSprintoContext(unittest.TestCase):
    def test_section_2_is_loaded_and_reaches_the_prompts(self):
        import sprinto_context
        ctx = sprinto_context.load()
        self.assertIn("## 2. About Sprinto", ctx["sprinto_facts"]); self.assertIn("Do not state any of them in an email", ctx["sprinto_facts"])
        self.assertNotIn("## 3.", ctx["sprinto_facts"]); self.assertEqual(len(ctx["sha256"]), 64)
        saved = R.SPRINTO_FACTS
        try:
            sprinto_context.apply(R)
            facts = {"company_name": "Acme", "evidence_text": "x"}; contact = {"name": "A B", "position": "CTO"}
            hyp = {"observed_fact": "f", "evidence": {"source_url": "u", "excerpt": "e"}, "hypothesis": "h", "capability": "evidence collection"}
            self.assertIn("Foundation (first certification) and Growth", R.draft_prompt(facts, contact, hyp, "S"))
            self.assertIn("Foundation (first certification) and Growth", R.qa_prompt(facts, contact, hyp, {"subject": "s", "body": "b"}, 10))
        finally:
            R.SPRINTO_FACTS = saved
    def test_missing_context_file_stops_the_run(self):
        import sprinto_context
        with mock.patch.object(sprinto_context, "PATH", "/nonexistent/x.md"):
            with self.assertRaises(RuntimeError): sprinto_context.load()
    def test_without_facts_the_prompt_allows_only_the_brief_sentence(self):
        facts = {"company_name": "Acme", "evidence_text": "x"}; contact = {"name": "A B", "position": "CTO"}
        hyp = {"observed_fact": "f", "evidence": {"source_url": "u", "excerpt": "e"}, "hypothesis": "h", "capability": "evidence collection"}
        saved = R.SPRINTO_FACTS; R.SPRINTO_FACTS = ""
        try: self.assertIn("No Sprinto facts were supplied", R.draft_prompt(facts, contact, hyp, "S"))
        finally: R.SPRINTO_FACTS = saved
