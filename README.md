# Sprinto cold-outreach pipeline (draft-first)

Takes company domains, qualifies each against the ICP rubric, finds one contact through Hunter.io, drafts one email,
and waits for the Growth Lead to approve before anything is sent. Emails go **only** to `DEMO_EMAIL`.

## Run it

    cd ~/Documents/Sprinto_Cold_Outreach_Case/pipeline
    ./start.sh                      # review desk at http://127.0.0.1:8765 (sets itself up on first run)

Paste domains in the app (or put them in `domains.txt`, one per line) and press Run.
Terminal alternative, no app: `.venv/bin/python pipeline.py domains.txt` (add `--send-demo` to be asked before sending).

`.env` needs: `HUNTER_API_KEY`, `HUNTER_MAX_CALLS_PER_RUN`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `DEMO_EMAIL`, `SENDER_NAME`,
and `GMAIL_APP_PASSWORD` to actually send (optional `GMAIL_USER` if the sending account differs from `DEMO_EMAIL`).
Keys are never printed, logged or copied.

## Where things are

| File | What it does |
|---|---|
| `rubric.py` | Weights, gates, thresholds, limits, prompts. **Rules unchanged**; two prompt lines added (see below). |
| `pipeline.py` | Read pages, AI extracts, **code** verifies and decides, Hunter contact, pain points, email, QA. |
| `review.py` | Approve / edit / reject, decision trail appended to the demo email. |
| `mailer.py` | Gmail SMTP. Recipient is read from `.env`, not a parameter. No CC/BCC. |
| `metrics.py` | Scorecard and shut-down checks. |
| `app.py`, `static/` | The review desk. All colours are in `static/theme.css`. |
| `output/<domain>/state.json` | Evidence, scores, retries, tokens, status, escalation reason, review. |
| `output/cache/` | Page cache (7 days) and Hunter results (kept until you delete them). |
| `tests/` | Offline tests with fake web/AI/Hunter: `.venv/bin/python -W ignore -m unittest discover -s tests` |

## What changed from the first draft (bugs fixed)

- Excerpts are checked against the page they cite, in full, not just the first 80 characters. Unverifiable claims count as 0.
- A "not SaaS / competitor / unrelated" call needs a verified proof excerpt, otherwise it is ignored (it would wrongly drop a company).
- `reason_now` windows (6 and 12 months) are now enforced in code from the dated evidence.
- Hunter: key sent in a header (not in the URL). Per Hunter's docs **403 = rate limit, 429 = quota** (the reverse of usual). Credits are counted from the response header. Free `/v2/account` shows credits left. A quota or rate pause stops all further lookups in that run. Only `type=personal` results are requested.
- Gemini: "thinking" parts are skipped, token use is logged, bad JSON retried, key never in errors.
- Domain matching no longer accepts `evil-acme.com` for `acme.com`. LinkedIn and login/app pages are never fetched.
- Contact role is taken from the job title by code (compliance > security > CTO > founder), not from the AI.
- Email checks added in code: word count, exactly one question, first-name greeting, no "Re:/following up", and any number in the email must be in the verified evidence.
- State is saved after every stage, so the app shows live progress and a crash loses nothing.
- Port 5000 is used by macOS AirPlay on this Mac, so the app uses **8765**.

Prompt edits in `rubric.py` (wording only): the AI must give ISO dates, and must copy proof into `disqualifier_evidence` when it claims a disqualifier.

## Assumptions I made (change any of them)

1. **Status for a gate that is not met but not disproved** (for example business fit level 1): `INSUFFICIENT_EVIDENCE`. Only *explicit* evidence against a gate gives a hard result. Explicit non-SaaS = `DISQUALIFIED`; explicit no-compliance-workload = `LOW_PRIORITY`.
2. **A blocked or unreadable homepage** = `INSUFFICIENT_EVIDENCE` with a retrieval-failure flag and reason, never a fit judgement.
3. **"Rewrite" = you edited the draft or rejected it.** A heavy rewrite keeps under 70% of the original text.
4. **Review time** = from opening a draft to your decision. Pipeline run time is shown separately because it runs unattended.
5. **"Running it takes more time than it saves"** = average review time at or above the 15-minute low end of your manual baseline.
6. **Contact accuracy** = your Yes/No on "Right person?" in the app. No verdict, no number.
7. Shut-down verdicts need at least **5** reviewed drafts; below that the scorecard says "not enough data".
8. Cost: Gemini cost is *tokens x paid-tier price* (free tier is really $0); Hunter is shown in credits (the free plan has no dollar cost).
9. The sending account defaults to `DEMO_EMAIL` itself.
10. Hunter results are cached with no expiry (credits are scarce). Delete `output/cache/hunter_<domain>.json` to refresh one.

## Known limits

- Pages that need JavaScript to show content come back thin; the run then ends `INSUFFICIENT_EVIDENCE`, not a poor-fit call.
- `robots.txt` is not read. Dates the AI extracts from pages are not independently verified (news dates come from the feed).
- The AI checks its own email as well as code does; code checks are the stricter ones. A human still approves every send.
- `gemini-2.5-flash` is marked deprecated by Google and redirects to a newer model from 1 Dec 2026. Change `GEMINI_MODEL` then.
