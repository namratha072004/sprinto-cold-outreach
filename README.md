# Sprinto cold outreach: draft-first review desk

The Growth Lead presses **Run**. The system searches Google News for companies that match Sprinto's ICP, researches each shortlisted
company (its website and Google News), decides go or no-go with **plain code** (the AI only reads and writes), finds the right contact
through Hunter.io, writes a personalised email, and puts it in two places: this dashboard and the Growth Lead's **Gmail Drafts**.
**The software never sends email.** Approve, Edit and Reject only record her decision; she sends from Gmail herself.

Source of truth: `context/sprinto_context.md` (the case and the confirmed decisions) and the rubric stored in Neon
(`scripts/seed_rubric.py` loads it from `Sprinto_Agent_Complete_Rubric.pdf`). No rubric value is hardcoded in the running code.

## Run it locally
    cp .env.example .env            # then fill in the values
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python scripts/seed_rubric.py       # applies schema.sql and loads the rubric into Neon (safe to re-run)
    .venv/bin/python scripts/check_keys.py        # tests every key; prints PASS/FAIL only, never a secret
    ./start.sh                                    # review desk at http://127.0.0.1:8765

One company from the terminal: `.venv/bin/python run_one.py cal.com`.
Gmail sign-in (the token lasts 7 days while the Google app is in Testing mode): `.venv/bin/python scripts/gmail_authorize.py --incognito`.

## Files
| File | Job |
|---|---|
| `schema.sql` | Neon tables: rubric versions, runs, discovery results, shortlist, companies, evidence, rounds, scores, contacts, pain hypotheses, emails, run log |
| `rubric_db.py`, `sprinto_context.py` | load the active rubric from Neon and the context file |
| `discovery.py` | Google News search (queries stored in Neon); company names read from headlines and verified by code; websites never guessed |
| `scraper.py`, `research.py` | website + Google News reading under the rubric's page budget, robots.txt, retries; evidence with labels and dates saved |
| `pipeline.py` | verification, scoring checks, gates (`decide`), contact scoring, pain points, email QA. Older file-based runner code in it is legacy |
| `outreach.py` | Hunter contact (capped, cached) -> pain points -> email -> Gmail draft |
| `gmail_drafts.py` | creates or replaces one draft per company. Cannot send: no send call exists |
| `review.py`, `metrics.py`, `app.py`, `static/` | the review desk; colours are in `static/theme.css` |
| `tests/` | `.venv/bin/python -W ignore -m unittest discover -s tests` (the desk tests use a throwaway schema in Neon) |

## Deploy on Vercel
`app.py` exports `app` (Flask). Add the variables listed in `.env.example` (all except `NEON_API_KEY`) in Vercel, then redeploy.
Each request must finish within the function limit (300 s on Hobby); the Run button finishes in about a minute.

## Known limits
- Google News is read through its public RSS feed, which Google's terms and robots.txt do not allow. The Growth Lead decided to use it anyway
  (recorded in the context file and in the run log). It is read gently: 2 s between requests, cached for a day.
- The shortlist step is not built yet: it waits for the Growth Lead's OK. Run currently stops after discovery.
- Most news results do not state a company's website, so most stay "unknown".
