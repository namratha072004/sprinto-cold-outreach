"""Builds TEST-FIXTURE results (fake companies, fake AI answers) into a scratch folder so the app can be
checked without spending credits. Never writes to the real output/ folder.
Usage: SPRINTO_OUT=/some/scratch/dir .venv/bin/python tests/seed_fixture.py"""
import os, sys
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
target = os.environ.get("SPRINTO_OUT")
assert target and "output" not in os.path.basename(target.rstrip("/")) or target, "set SPRINTO_OUT to a scratch folder"
import tests.test_logic as T
import settings as S, pipeline as P
S.OUT, S.CACHE, P.OUT = target, os.path.join(target, "cache"), target
os.makedirs(S.CACHE, exist_ok=True)
os.environ.update({"DEMO_EMAIL": "demo@example.test", "HUNTER_API_KEY": "x", "GEMINI_API_KEY": "x", "SENDER_NAME": "Test Sender"})

def gem(company, **over):
    def f(prompt, usage):
        if prompt.startswith("TASK=ASSESS"):
            a = T.good_assessment(company_name=company)
            for k, v in over.items(): a[k] = v
            return a
        return T.fake_gemini(prompt, usage)
    return f
def http(url, timeout=15):
    return T.fake_http(url.replace("seedco.test", "acme.test").replace("lowco.test", "acme.test"), timeout)
fake_r, _ = T.TestHunter._get(T.TestHunter())
S.write_state  # noqa
with mock.patch.object(P, "http_get", http), mock.patch.object(P.requests, "get", fake_r):
    P.hunter_reset()
    with mock.patch.object(P, "gemini_json", side_effect=gem("Acme (TEST FIXTURE)")): P.run_domain("acme.test")
    seed = T.good_assessment()["funding"]; seed = dict(seed, stage="Seed")
    with mock.patch.object(P, "gemini_json", side_effect=gem("Seedco (TEST FIXTURE)", funding=seed)): P.run_domain("seedco.test")
    low = T.good_assessment()["dimensions"]
    for k in ("customer_trust", "reason_now"): low[k] = {"level": 0, "state": "missing", "reasoning": "", "evidence": []}
    with mock.patch.object(P, "gemini_json", side_effect=gem("Lowco (TEST FIXTURE)", dimensions=low)): P.run_domain("lowco.test")
with mock.patch.object(P, "http_get", T.fake_http): P.run_domain("blocked.test")
print("seeded", S.OUT)
