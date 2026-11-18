"""Offline tests: fake web, fake Gemini, fake Hunter. No network, no credits, no keys.
Run:  .venv/bin/python -W ignore -m unittest discover -s tests -v
All fake companies below are TEST FIXTURES, not real data."""
import os, sys, json, tempfile, shutil, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import settings as S
TMP = tempfile.mkdtemp()
S.OUT, S.CACHE = TMP, os.path.join(TMP, "cache")
import pipeline as P, rubric as R, review, mailer, metrics
P.OUT = TMP

HOME = ("Acme Cloud | Acme is a cloud software platform for logistics teams. Our SaaS product stores customer data in AWS. "
        "Read our security page and customers page.")
SEC = "Security at Acme. We are preparing for SOC 2 Type II and run continuous control monitoring across our cloud."
CUST = "Customers. Trusted by enterprise customers such as Globex and Initech in regulated industries."
NEWS_FUNDING = "2026-09-10 | Acme raises Series B to expand enterprise logistics | publisher: TechNews"

class Resp:
    def __init__(self, text, url, status=200):
        self.text, self.url, self.status_code, self.content, self.headers = text, url, status, text.encode(), {"content-type": "text/html"}

def html(text, links=()):
    return "<html><head><title>T</title></head><body><p>%s</p>%s</body></html>" % (text, "".join(f'<a href="{h}">{a}</a>' for h, a in links))

def fake_http(url, timeout=15):
    if "news.google.com" in url:
        return "ok", Resp(f"<rss><channel><item><title>Acme raises Series B to expand enterprise logistics</title><link>https://news.example/acme-b</link>"
                          f"<pubDate>Thu, 10 Sep 2026 10:00:00 GMT</pubDate><source>TechNews</source></item></channel></rss>", url), 1
    if url.rstrip("/") == "https://acme.test":
        return "ok", Resp(html(HOME, [("/security", "Security"), ("/customers", "Customers")]), "https://acme.test/"), 1
    if url.endswith("/security"):
        return "ok", Resp(html(SEC), url), 1
    if url.endswith("/customers"):
        return "ok", Resp(html(CUST), url), 1
    if "blocked.test" in url:
        return "blocked", None, 1
    return "failed", None, 1

def ev(url, ex, dated=None):
    return {"source_url": url, "excerpt": ex, "dated": dated}

def good_assessment(**over):
    a = {"company_name": "Acme", "business_model": "saas", "is_direct_compliance_platform_competitor": False, "is_unrelated_business": False,
         "disqualifier_evidence": None,
         "funding": {"stage": "Series B", "date": "2026-09-10", "evidence": ev("https://news.example/acme-b", "Acme raises Series B to expand enterprise logistics")},
         "employee_count": {"value": None, "evidence": None},
         "dimensions": {
             "business_fit": {"level": 2, "state": "positive", "reasoning": "r", "evidence": [ev("https://acme.test/", "Acme is a cloud software platform for logistics teams")]},
             "customer_trust": {"level": 2, "state": "positive", "reasoning": "r", "evidence": [ev("https://acme.test/customers", "Trusted by enterprise customers such as Globex and Initech")]},
             "compliance_workload": {"level": 2, "state": "positive", "reasoning": "r", "evidence": [ev("https://acme.test/security", "We are preparing for SOC 2 Type II and run continuous control monitoring")]},
             "reason_now": {"level": 1, "state": "positive", "reasoning": "r", "evidence": [ev("https://news.example/acme-b", "Acme raises Series B to expand enterprise logistics", "2026-09-10")]},
             "delivery_complexity": {"level": 0, "state": "missing", "reasoning": "", "evidence": []}},
         "compliance_claims_exact": [], "named_customers": [], "hiring_signals": [], "missing": []}
    a.update(over)
    return a

def hunter_payload():
    return {"data": {"emails": [
        {"value": "sam@acme.test", "first_name": "Sam", "last_name": "Lee", "position": "Marketing Manager", "department": "marketing",
         "seniority": "senior", "confidence": 99, "verification": {"status": "valid"}, "sources": [{"uri": "https://acme.test/team", "last_seen_on": "2026-10-01", "still_on_page": True}]},
        {"value": "priya@acme.test", "first_name": "Priya", "last_name": "Rao", "position": "Head of Security", "department": "it",
         "seniority": "executive", "confidence": 90, "verification": {"status": "valid"}, "sources": [{"uri": "https://acme.test/team", "last_seen_on": "2026-10-01", "still_on_page": True}]},
        {"value": "dev@acme.test", "first_name": "Dev", "last_name": "Patel", "position": "CTO", "department": "it",
         "seniority": "executive", "confidence": 95, "verification": {"status": "valid"}, "sources": [{"uri": "https://acme.test/team", "last_seen_on": "2026-10-01", "still_on_page": True}]}]}}

def fake_gemini(prompt, usage):
    usage["calls"] = usage.get("calls", 0) + 1
    t = prompt.split("\n")[0]
    if t == "TASK=ASSESS":
        return good_assessment()
    if t == "TASK=CONTACTS":
        full = {"company_association": 2, "functional_ownership": 2, "ability_to_drive_action": 2, "match_to_need": 2, "identity_quality": 1}
        return {"candidates": [
            {"index": 0, "scores": {"company_association": 2, "functional_ownership": 0, "ability_to_drive_action": 2, "match_to_need": 2, "identity_quality": 2}, "reasoning": "marketing"},
            {"index": 1, "scores": full, "reasoning": "security head"},
            {"index": 2, "scores": full, "reasoning": "cto"}]}
    if t == "TASK=PAIN":
        h = lambda text, cap, ex: {"observed_fact": "Acme is preparing for SOC 2 Type II", "evidence": ev("https://acme.test/security", ex), "hypothesis": text,
                                   "role_relevance": "x", "possible_consequence": "y", "capability": cap,
                                   "scores": {"account_specific_evidence": 3, "relevance_to_person": 2, "plausible_business_consequence": 1, "capability_match": 1, "honest_wording": 1}}
        return {"hypotheses": [h("Evidence gathering may be manual", "evidence collection", "We are preparing for SOC 2 Type II"),
                               h("Monitoring may be stretched across clouds", "continuous control monitoring", "run continuous control monitoring across our cloud")]}
    if t == "TASK=DRAFT":
        body = ("Hi Priya, I saw that Acme is preparing for SOC 2 Type II while running continuous control monitoring across its cloud. "
                "Preparing for an audit while enterprise customers like Globex ask for proof can mean a lot of evidence gathering, and that may be taking "
                "time from the security team. Sprinto automates evidence collection for SOC 2, ISO 27001 and GDPR so the team spends less time chasing screenshots "
                "and more time on real security work. We keep the audit trail current in one place, so the auditor reads from a single source. "
                "Is evidence collection something your team is currently planning to improve? Thanks, Test Sender")
        return {"subject": "SOC 2 prep at Acme", "body": body}
    if t == "TASK=QA":
        return {"scores": {"specific_hook": 2, "useful_hypothesis": 2, "relevant_value": 2, "simple_next_step": 2, "clarity": 2},
                "mandatory": {k: True for k in ["correct_person_and_company", "evidence_for_every_factual_claim", "hypothesis_phrased_tentatively",
                              "no_invented_metrics_relationships_urgency_guarantees", "no_false_re_or_following_up", "no_unsupported_compliance_deficiency",
                              "addressed_by_name", "one_primary_pain"]}, "feedback": ""}
    raise AssertionError("unexpected prompt " + t)

class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True); os.makedirs(S.CACHE)
        P.hunter_reset()
        self.env = mock.patch.dict(os.environ, {"DEMO_EMAIL": "demo@example.test", "HUNTER_API_KEY": "k", "HUNTER_MAX_CALLS_PER_RUN": "5",
                                               "GEMINI_API_KEY": "k", "SENDER_NAME": "Test Sender"}); self.env.start()
    def tearDown(self):
        self.env.stop()

class TestDecide(Base):
    def a(self, **lv):
        a = good_assessment()
        for k, (lvl, state) in lv.items():
            a["dimensions"][k].update(level=lvl, state=state)
        return a
    def test_qualified(self):
        d = P.decide(self.a())
        self.assertEqual(d["status"], "QUALIFIED"); self.assertEqual(d["total"], 25 + 20 + 25 + 10)  # 80
    def test_unknown_funding_flags_and_continues(self):
        a = good_assessment(); a["funding"] = {"stage": "unknown", "date": None, "evidence": None}
        d = P.decide(a)
        self.assertEqual(d["status"], "QUALIFIED"); self.assertIn("funding_stage_unknown", d["flags"])
    def test_outside_series_is_disqualified(self):
        for stage in ("seed", "series d+", "public"):
            a = good_assessment(); a["funding"]["stage"] = stage
            self.assertEqual(P.decide(a)["status"], "DISQUALIFIED", stage)
    def test_not_saas_disqualified(self):
        self.assertEqual(P.decide(good_assessment(business_model="not_saas"))["status"], "DISQUALIFIED")
    def test_competitor_disqualified(self):
        self.assertEqual(P.decide(good_assessment(is_direct_compliance_platform_competitor=True))["status"], "DISQUALIFIED")
    def test_missing_gate_evidence_is_insufficient_not_poor_fit(self):
        self.assertEqual(P.decide(self.a(compliance_workload=(0, "missing")))["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(P.decide(self.a(business_fit=(1, "positive")))["status"], "INSUFFICIENT_EVIDENCE")
    def test_supported_but_below_threshold_is_low_priority(self):
        d = P.decide(self.a(customer_trust=(0, "missing"), reason_now=(0, "missing"), delivery_complexity=(0, "missing")))
        self.assertEqual(d["total"], 50); self.assertEqual(d["status"], "LOW_PRIORITY")
    def test_threshold_boundary_65(self):
        a = self.a(customer_trust=(1, "positive"), reason_now=(1, "positive"), delivery_complexity=(0, "missing"))   # 25+10+25+10 = 70
        self.assertEqual(P.decide(a)["status"], "QUALIFIED")
        a = self.a(customer_trust=(1, "positive"), reason_now=(0, "missing"), delivery_complexity=(0, "missing"))    # 60
        self.assertEqual(P.decide(a)["status"], "LOW_PRIORITY")

class TestVerification(Base):
    def test_invented_excerpt_is_zeroed_and_real_kept(self):
        sources = {"https://acme.test/": HOME, "https://acme.test/security": SEC, "https://acme.test/customers": CUST,
                   "https://news.example/acme-b": NEWS_FUNDING}
        raw = good_assessment()
        raw["dimensions"]["compliance_workload"]["evidence"] = [ev("https://acme.test/security", "We hold SOC 2 Type II and ISO 27001 certification")]  # invented
        raw["funding"]["evidence"] = ev("https://acme.test/", "Acme raised a Series B")                                                                  # invented
        with mock.patch.object(P, "gemini_json", return_value=raw):
            a = P.assess("acme.test", sources, {})
        self.assertEqual(a["dimensions"]["compliance_workload"]["level"], 0)
        self.assertEqual(a["dimensions"]["compliance_workload"]["state"], "missing")
        self.assertEqual(a["funding"]["stage"], "unknown")
        self.assertEqual(a["dimensions"]["business_fit"]["level"], 2)
        self.assertTrue(a["verification_notes"])
    def test_excerpt_from_wrong_page_is_rejected(self):
        v = P.Verifier({"https://a.test/x": "alpha beta gamma delta epsilon", "https://a.test/y": "other words entirely here"})
        self.assertTrue(v.ok("https://a.test/x", "alpha beta gamma delta"))
        self.assertFalse(v.ok("https://a.test/y", "alpha beta gamma delta"))
    def test_reason_now_window_and_undated(self):
        sources = {"https://acme.test/": HOME, "https://news.example/acme-b": NEWS_FUNDING}
        raw = good_assessment()
        raw["dimensions"]["reason_now"] = {"level": 2, "state": "positive", "reasoning": "", "evidence": [ev("https://news.example/acme-b", "Acme raises Series B to expand enterprise logistics", "2024-01-05")]}
        with mock.patch.object(P, "gemini_json", return_value=raw):
            self.assertEqual(P.assess("acme.test", sources, {})["dimensions"]["reason_now"]["level"], 0)
        raw["dimensions"]["reason_now"]["evidence"][0]["dated"] = None
        with mock.patch.object(P, "gemini_json", return_value=raw):
            self.assertEqual(P.assess("acme.test", sources, {})["dimensions"]["reason_now"]["level"], 0)
    def test_unproven_disqualifier_is_ignored(self):
        raw = good_assessment(business_model="not_saas", is_unrelated_business=True)
        with mock.patch.object(P, "gemini_json", return_value=raw):
            a = P.assess("acme.test", {"https://acme.test/": HOME}, {})
        self.assertEqual(a["business_model"], "unclear"); self.assertFalse(a["is_unrelated_business"])

class TestHunter(Base):
    def _get(self, status=200, headers=None, payload=None):
        calls = []
        def fake(url, **kw):
            calls.append(url)
            r = mock.Mock(); r.status_code = status; r.headers = headers or {"X-Credits-Used": "1"}
            r.json.return_value = payload if payload is not None else hunter_payload()
            r.ok = status == 200
            if "account" in url:
                r.status_code = 200; r.ok = True; r.json.return_value = {"data": {"requests": {"credits": {"remaining": 50}}}}
            return r
        return fake, calls
    def test_picks_by_role_not_first_result_and_caches(self):
        fake, calls = self._get()
        st = {}
        with mock.patch.object(P.requests, "get", fake), mock.patch.object(P, "gemini_json", side_effect=fake_gemini):
            c = P.pick_contact("Acme", "acme.test", st, {})
            c2 = P.pick_contact("Acme", "acme.test", {}, {})
        self.assertEqual(c["name"], "Priya Rao")            # security leader outranks CTO; marketing (first) failed ownership
        self.assertEqual(c["score"], 9)
        self.assertEqual(len([u for u in calls if "domain-search" in u]), 1)   # second lookup came from cache
        self.assertEqual(P.HUNTER["credits_used"], 1.0)
        self.assertEqual(c2["name"], "Priya Rao")
    def test_no_passing_candidate(self):
        fake, _ = self._get()
        weak = lambda p, u: {"candidates": [{"index": i, "scores": {"company_association": 1, "functional_ownership": 1, "ability_to_drive_action": 1, "match_to_need": 1, "identity_quality": 1}, "reasoning": ""} for i in range(3)]}
        st = {}
        with mock.patch.object(P.requests, "get", fake), mock.patch.object(P, "gemini_json", side_effect=weak):
            self.assertIsNone(P.pick_contact("Acme", "acme.test", st, {}))
        self.assertEqual(st["status"], "NO_SUITABLE_CONTACT")
    def test_429_pauses_run_and_stops_further_calls(self):
        fake, calls = self._get(status=429)
        s1, s2 = {}, {}
        with mock.patch.object(P.requests, "get", fake):
            P.pick_contact("A", "a.test", s1, {}); P.pick_contact("B", "b.test", s2, {})
        self.assertEqual(s1["status"], "CONTACT_LOOKUP_PAUSED"); self.assertEqual(s2["status"], "CONTACT_LOOKUP_PAUSED")
        self.assertEqual(len([u for u in calls if "domain-search" in u]), 1)    # second company made no call
    def test_403_is_rate_limit_per_docs(self):
        fake, _ = self._get(status=403); st = {}
        with mock.patch.object(P.requests, "get", fake):
            P.pick_contact("A", "a.test", st, {})
        self.assertIn("rate limit", st["reason"])
    def test_hard_cap(self):
        fake, calls = self._get(payload={"data": {"emails": []}})
        with mock.patch.dict(os.environ, {"HUNTER_MAX_CALLS_PER_RUN": "2"}), mock.patch.object(P.requests, "get", fake):
            sts = [{} for _ in range(4)]
            for i, s in enumerate(sts):
                P.pick_contact("X", f"d{i}.test", s, {})
        self.assertEqual(len([u for u in calls if "domain-search" in u]), 2)
        self.assertEqual(sts[2]["status"], "CONTACT_LOOKUP_PAUSED")

class TestEmailChecks(Base):
    C = {"first_name": "Priya", "name": "Priya Rao"}
    def test_catches_problems(self):
        body = "Hello team, following up on our chat. We cut audit time by 40%. Can we talk? Is now good?"
        words, problems = P.code_checks({"subject": "Re: hi", "body": body}, self.C, {}, "SOC 2 prep")
        joined = " ".join(problems)
        for needle in ("word count", "exactly one question", "first name", "numbers", "re:"):
            self.assertIn(needle, joined.lower())
    def test_framework_numbers_allowed(self):
        body = "Hi Priya, " + "word " * 85 + "Sprinto automates SOC 2 and ISO 27001 evidence. Is that useful?"
        _, problems = P.code_checks({"subject": "SOC 2", "body": body}, self.C, {}, "")
        self.assertEqual(problems, [])

class TestMailer(Base):
    def test_only_demo_address_no_cc_bcc(self):
        smtp = mock.MagicMock(); smtp.__enter__.return_value = smtp
        with mock.patch.dict(os.environ, {"GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop", "GMAIL_ADDRESS": "sender@example.test"}), mock.patch.object(mailer.smtplib, "SMTP_SSL", return_value=smtp):
            mailer.send_demo("s", "b")
        msg = smtp.send_message.call_args[0][0]
        self.assertEqual(msg["To"], "demo@example.test"); self.assertIsNone(msg["Cc"]); self.assertIsNone(msg["Bcc"])
        self.assertEqual(smtp.send_message.call_args[1]["to_addrs"], ["demo@example.test"])
        smtp.login.assert_called_with("sender@example.test", "abcdefghijklmnop")   # sender account differs; recipient is still the demo address
    def test_no_password_means_not_sent(self):
        with mock.patch.dict(os.environ, {"GMAIL_APP_PASSWORD": ""}), mock.patch.object(S, "_ENV", {}):
            with self.assertRaises(mailer.MailerNotConfigured):
                mailer.send_demo("s", "b")

class TestEndToEnd(Base):
    def run_e2e(self):
        fake_r, _ = TestHunter._get(self, )
        with mock.patch.object(P, "http_get", fake_http), mock.patch.object(P.requests, "get", fake_r), mock.patch.object(P, "gemini_json", side_effect=fake_gemini):
            return P.run_domain("acme.test")
    def test_full_run_makes_reviewable_draft(self):
        st = self.run_e2e()
        self.assertEqual(st["status"], "DRAFT_READY", st.get("reason"))
        self.assertEqual(st["contact"]["name"], "Priya Rao")
        self.assertEqual(st["total"], 80)
        self.assertTrue(all(g["state"] == "passed" for g in st["gates"].values()))
        self.assertEqual(len(st["pain_points"]), 2)
        self.assertTrue(st["rounds"][0]["new_evidence"])
        self.assertIn("seconds", st["timing"])
        self.assertTrue(os.path.exists(S.state_path("acme.test")))
        # nothing was sent: no review yet
        self.assertNotIn("decision", st.get("review", {}))
    def test_approve_without_gmail_credentials_saves_but_does_not_send(self):
        self.run_e2e()
        d = S.read_state("acme.test")["draft"]
        with mock.patch.dict(os.environ, {"GMAIL_APP_PASSWORD": ""}), mock.patch.object(S, "_ENV", {}):
            st = review.approve("acme.test", d["subject"], d["body"] + " edited")
        self.assertEqual(st["review"]["send_status"], "NOT_SENT"); self.assertTrue(st["review"]["edited"])
        self.assertEqual(review.view_status(st)["key"], "APPROVED_NOT_SENT")
        with self.assertRaises(ValueError):                       # cannot approve twice
            review.approve("acme.test", d["subject"], d["body"])
    def test_approve_sends_once(self):
        self.run_e2e()
        d = S.read_state("acme.test")["draft"]
        smtp = mock.MagicMock(); smtp.__enter__.return_value = smtp
        with mock.patch.dict(os.environ, {"GMAIL_APP_PASSWORD": "pw"}), mock.patch.object(mailer.smtplib, "SMTP_SSL", return_value=smtp):
            st = review.approve("acme.test", d["subject"], d["body"])
            review.retry_send("acme.test")
        self.assertEqual(st["review"]["send_status"], "SENT"); self.assertEqual(smtp.send_message.call_count, 1)
        sent = smtp.send_message.call_args[0][0]
        self.assertTrue(sent["Subject"].startswith("[DEMO] ")); self.assertIn("priya@acme.test", sent.get_content())  # research data only in the body
        self.assertEqual(sent["To"], "demo@example.test")
    def test_blocked_homepage_is_retrieval_failure_not_poor_fit(self):
        with mock.patch.object(P, "http_get", fake_http), mock.patch.object(P, "gemini_json", side_effect=fake_gemini):
            st = P.run_domain("blocked.test")
        self.assertEqual(st["status"], "INSUFFICIENT_EVIDENCE"); self.assertIn("retrieval_failure", st["flags"])
        self.assertIn("not a judgement on fit", st["reason"])
    def test_one_company_error_does_not_stop_batch(self):
        def boom(prompt, usage):
            if "boom.test" in prompt: raise RuntimeError("model exploded")
            return fake_gemini(prompt, usage)
        fake_r, _ = TestHunter._get(self)
        def http(url, timeout=15):
            return fake_http(url.replace("boom.test", "acme.test"), timeout)
        with mock.patch.object(P, "http_get", http), mock.patch.object(P.requests, "get", fake_r), mock.patch.object(P, "gemini_json", side_effect=boom):
            rows = P.run_batch(["boom.test", "acme.test"])
        self.assertEqual([r["status"] for r in rows], ["ERROR", "DRAFT_READY"])
    def test_metrics_after_review(self):
        self.run_e2e()
        d = S.read_state("acme.test")["draft"]
        with mock.patch.dict(os.environ, {"GMAIL_APP_PASSWORD": ""}), mock.patch.object(S, "_ENV", {}):
            review.mark_opened("acme.test"); review.approve("acme.test", d["subject"], d["body"])
        review.contact_feedback("acme.test", True)
        m = metrics.compute()
        self.assertEqual(m["drafts_reviewed"], 1); self.assertEqual(m["rewrite_rate_pct"], 0.0)
        self.assertEqual(m["contact_accuracy_pct"], 100.0); self.assertFalse(m["kill_checks"]["enough_data"])

class TestFetchRules(Base):
    def test_never_linkedin_or_login(self):
        self.assertFalse(P.fetch_allowed("https://www.linkedin.com/company/acme"))
        self.assertFalse(P.fetch_allowed("https://acme.test/login"))
        self.assertTrue(P.fetch_allowed("https://acme.test/security"))
    def test_domain_match_not_substring(self):
        self.assertTrue(P.host_matches("www.acme.test", "acme.test")); self.assertFalse(P.host_matches("evil-acme.test", "acme.test"))
    def test_domain_cleaning(self):
        self.assertEqual(P.clean_domain("https://www.Acme.com/pricing"), "acme.com")
        self.assertTrue(P.clean_domain("not a domain").startswith("INVALID"))
        self.assertIsNone(P.clean_domain("# comment"))

if __name__ == "__main__":
    unittest.main()
