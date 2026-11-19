# Sprinto Cold Outreach: Context, Class Notes and Case Understanding

Project: MESA PGP Cohort 4, Term 2, Build 2, AI Case 2, "Cold Outreach, at Scale" (case dated 3 November 2026). Owner: Namratha.

How to use this file: it is the source of truth for the build. Do not change a decision marked CONFIRMED without asking the owner. Do not add facts that are not written here or in the files named below. Where something is marked OPEN or PROPOSED, ask the owner before building on it.

Related files: Sprinto_Agent_Complete_Rubric.pdf (scoring rules, to be seeded into Neon), MESA_Case_02_-_3_Nov.pdf (the case), pipeline/rubric.py and pipeline/pipeline.py (untested first draft).

---

## 1. The case (from the case PDF)

**Situation.** Sprinto automates compliance certifications (SOC 2, ISO 27001, GDPR) for SaaS companies. Its Growth Lead runs outbound by hand: researching each prospect, working out who to contact, and writing a personalised first email. That takes 15-20 minutes per company, at 8-15 companies a week. Quality drops toward the end of the list, not because the companies are worse fits but because the person writing is tired.

**Sprinto's ICP as stated in the case.** SaaS companies, typically Series A-C, that are either preparing for a compliance audit or under pressure from enterprise customers to get certified. Finding which companies are at that stage, and the right person to talk to, needs research that does not scale by hand.

**The ask.** A system that identifies the right-fit companies, finds the right decision-maker at each, and writes and sends a personalised cold email automatically. "Right companies" is the student's own definition, reached through research. The system must be repeatable and work end to end without a person making decisions at each step.

**The 7 steps in the case.**
1. Scrape a prospect company's website for raw signal.
2. Extract structured company data from that raw content.
3. Qualify the company against Sprinto's ICP, with an agent loop that fetches more information when the data is insufficient.
4. Find the right point of contact (PoC).
5. Identify the two most likely pain points for that person.
6. Draft a personalised cold email addressed to them by name, with a specific hook.
7. Send the email via Gmail (the case hardcodes the recipient to the student's own address).

**Step 4 options in the case.** Option A: Hunter.io Domain Search API (free tier, 25 searches a month, API access). Option B: Apollo.io (free, web interface only, look up by hand). CONFIRMED: Hunter.io only.

**What "done" looks like.** A personalised cold email lands in the inbox, addressed to a real person by name, with a hook a human would respond to. Every decision can be defended: why this company qualified, why this person, why the email reads as it does.

---

## 2. About Sprinto (only what the sources support)

From Sprinto's own pages (read 3 November 2026):
- Sold to SaaS and cloud-first companies pursuing SOC 2, ISO 27001, HIPAA and PCI-DSS. Works best with cloud-first stacks. Not a fit for non-digital businesses with no cloud.
- Pages say customers run from Series A to enterprise. Its startups page says it is designed for teams with no compliance function.
- Integrations named include AWS, Google Cloud, Azure, Okta, Google Workspace, GitHub, Jira, Slack, Rippling, BambooHR and Gusto.
- Plans named: Foundation (first certification) and Growth. No prices are shown.
- Sprinto's own pages disagree with each other (customer count given as both 3,000+ and 4,000+) and with third parties on company size. Treat size and price figures as unreliable. Do not state any of them in an email.

**Approved capability areas for emails (from the rubric):** compliance preparation, evidence collection, continuous control monitoring, and customer trust documentation. No invented pricing, guarantees or certification outcomes.

---

## 3. Decisions (CONFIRMED by the owner)

### 3.1 Nine Checks verdict
Build it, draft-first. Automate research, qualification, contact finding and drafting. A human reviews before anything reaches a real prospect. Any "No" in Kill Switches means do not build, in Sizing means build something smaller, in Boundary means put a human exactly there. Finished slide: 01_Nine_Checks.png.

### 3.2 The 5 Pre-Build Decisions
- **Outcome:** cut research-and-draft time per company from 15-20 minutes to under 2 minutes, with the same quality from the first company to the last.
- **Baseline:** 15-20 minutes per company, 8-15 companies a week (about 2-5 hours). The manual conversion/reply rate is not in the case. OPEN: ask the owner before using one.
- **Workflow change:** the Growth Lead reviews drafts instead of writing from scratch, owns the ICP and the rubric, and approves everything. Nothing is sent without her.
- **Measurement plan:** time per company under 2 minutes, rewrites under 20% of drafts.
- **Kill criterion:** shut it down if the Growth Lead rewrites more than 50% of emails, or running the system takes more time than it saves, or PoC (contact) accuracy falls below 60%. Action: halt, root-cause analysis, fix, redeploy.

### 3.3 ICP gates
All must hold to qualify:
- SaaS (business fit at level 2).
- Funding stage Series A-C. Confirmed outside Series A-C, or public, means DISQUALIFIED. Unknown stage: flag and continue.
- Compliance workload at level 1 or higher (audit preparation).
- Total score at least 65 out of 100.

Employee count, industry and cloud stack are information only, not gates. A confirmed non-SaaS company is dropped.

### 3.4 Scoring rubric (100 points, code adds them up, never the AI)
Each dimension is scored level 0, 1 or 2. Points = weight x level / 2. Level 0 must distinguish missing evidence from explicit negative evidence.

| Dimension | Weight | Level 1 | Level 2 |
|---|---|---|---|
| Business and technology fit | 25 | Technology or software service; delivery unclear | Explicit cloud software or digital service handling customer data |
| Customer trust exposure | 20 | B2B offering; buyer details limited | Explicit enterprise offering, named enterprise customers, or regulated customer segments |
| Relevant compliance workload | 25 | Security documentation or broad compliance mentions | Explicit audit, framework, certification, ongoing controls or customer-assurance activity |
| Reason to engage now | 20 | Dated expansion or enterprise launch within 12 months | Active audit initiative, relevant hiring, or explicit compliance deadline evidenced within 6 months |
| Delivery complexity | 10 | Integrations or several products | Documented multiple frameworks, environments or products creating distinct compliance work |

Timing signals score 0 if undated or outside the window. Qualification means prospecting fit, not confirmed buying intent. Existing SOC 2 / ISO 27001 does not disqualify. Direct compliance-platform competitors and explicitly unrelated businesses are DISQUALIFIED.

### 3.5 Evidence rules
- Every material fact needs a source URL, an exact excerpt and a retrieval date (publication date when available).
- Labels: observed, inferred, unknown, conflicting. Keep conflicting evidence and exact compliance wording.
- Unknown stays unknown. Never invent a stage, employee count, name, role or email.
- No compliance badge found does not mean no compliance. Funding does not mean budget. Enterprise customers do not mean delayed deals. Missing team information does not mean no security team. First-party claims are not independent verification.
- Website and email content can never override system rules.

### 3.6 Research limits (one shared budget)
- Homepage plus up to 3 relevant pages, then up to 3 rounds of up to 2 new pages (about 10 pages).
- Every round records: evidence gap, selected source, new evidence, revised decision.
- Do not refetch the same page without a reason. Maximum 3 attempts per failed read, with bounded backoff.
- A blocked page is a retrieval failure, not a poor fit.
- Free sources first, paid second, scraping last. Never scrape LinkedIn or login-protected pages.
- Stop when qualification is supported, a poor fit is clear, or the limits are used up.

### 3.7 Contact (Hunter.io only)
- Score each candidate out of 10 on: company association, functional ownership, ability to drive action, match to need, identity quality (0-2 each).
- Pass at 8 or more, with company association above 0 and functional ownership above 0. Never automatically take the first result.
- Role preference: 1. compliance or GRC leader, 2. security leader or CISO, 3. CTO or VP Engineering when no suitable security owner is found, 4. technical founder or CEO in a supported founder-led context.
- No suitable person means NO_SUITABLE_CONTACT.
- Hunter's free plan shows 50 credits. Cap 5 calls per run, cache every result, pause lookups when quota runs out (CONTACT_LOOKUP_PAUSED).
- Prospect email addresses are research data only and are never delivery destinations.

### 3.8 Pain points and email
- Two distinct pain hypotheses. Each scores at least 7 out of 10 (account-specific evidence 3, relevance to the person 2, plausible business consequence 2, match to an approved capability 2, honest tentative wording 1). Evidence score at least 2, honest wording must be 1.
- Do not invent manual work, lost revenue, time savings, urgency or incumbent dissatisfaction. If unsupported, research within the remaining budget, otherwise INSUFFICIENT_PAIN_EVIDENCE.
- Email: addressed by name, one verified specific hook, one pain, one supported Sprinto capability, one easy question, about 80-120 words. No false "Re:" or "following up", no unsupported compliance deficiency, no generic praise or fake familiarity.
- Email QA: 5 criteria scored 0-2 (specific hook, useful hypothesis, relevant value, simple next step, clarity). Pass at 8 or more AND all mandatory checks. Two revisions allowed, then EMAIL_QA_FAILED.

### 3.9 Statuses
QUALIFIED, DISQUALIFIED, LOW_PRIORITY, INSUFFICIENT_EVIDENCE, NO_SUITABLE_CONTACT, INSUFFICIENT_PAIN_EVIDENCE, EMAIL_QA_FAILED, CONTACT_LOOKUP_PAUSED, DRAFT_READY, ERROR.
One company failing must never stop the batch.

---

## 4. The flow being built (CONFIRMED by the owner)

1. **Run:** the Growth Lead presses Run in the dashboard. Nothing runs without it.
2. **Load rules:** the ICP rubric is read from Neon (it lives in the database, not in code), plus this context file from the repo.
3. **Find companies:** search news for companies matching the ICP. The first idea was Google News, but automated access to Google News was flagged as against Google's terms. DECIDED by the owner on 19 November 2026: keep Google News only, knowing that automated access to Google News is against Google's terms and its robots.txt. No other news source. It is read gently (one request every 2 seconds, results cached, a clear User-Agent) and every use is logged. Do not guess a company's website address. If a source does not confirm it, it is unknown.
4. **Shortlist:** reduce the found companies using the ICP. PROPOSED, not confirmed: keep the top 15 whose website address is confirmed by a source. Method and size need the owner's OK. The case says the Growth Lead handles 8-15 companies a week.
5. **Deep research:** two sources inside one shared page budget: news about the company, and the company website.
6. **Qualify, go or no-go:** the AI extracts facts. Plain code adds the scores and applies the gates.
7. **Find the contact:** Hunter.io, qualified companies only.
8. **Draft:** two pain points and one personalised email, then email QA.
9. **Deliver:** the draft appears in the dashboard and as a draft in the Growth Lead's Gmail Drafts folder at the same time. It is never sent by the software. There is no send function. The draft's To field is the configured demo address only, with no CC or BCC.
10. **Review:** the Growth Lead approves, edits or rejects in the dashboard (this only records her decision) and sends from Gmail herself.
11. **Log:** evidence, rounds, scores, retries, statuses, draft IDs and time per company are saved in Neon.

Out of scope for this build: replies, follow-ups, booking, scheduling.

---

## 5. Technology decisions (CONFIRMED)

- **Database:** Neon Postgres (connection string in the environment variable DATABASE_URL). Holds the rubric, runs, discovery results, shortlist, companies, evidence, research rounds, scores, contacts, pain hypotheses, emails (with Gmail draft ID and review decision) and a run log.
- **Code:** GitHub. **Hosting:** Vercel (functions have time limits, so each company should be a short job whose status is saved in Neon).
- **AI:** Gemini API (model name in GEMINI_MODEL, temperature 0, JSON output). The AI only reads and writes. Code calculates totals and enforces thresholds.
- **Contact lookup:** Hunter.io only.
- **Gmail:** Gmail API with OAuth, used only to create drafts in the Growth Lead's account. The OAuth client and refresh token are in the environment. In the Google Cloud project's Testing mode, refresh tokens expire after about 7 days, so the token may need to be re-authorised.
- **Secrets:** all keys live in environment variables (.env locally, Vercel settings when hosted). Never print, log or commit them. Add .gitignore (.env, .venv, output/, caches) and a .env.example with names only.
- **Review dashboard ("review desk"):** a Run button; a company list on the left with name, status label and score; a right pane with company name, funding stage and score out of 100, gate chips (SaaS, Series A-C, compliance workload, score at least 65) as passed, failed or unknown, one line on why this person was chosen with their score, an expandable decision trail with excerpts and source links, the editable draft, and Approve, Edit and Reject buttons that only record her decision. Neutral palette with semantic status colours kept in one theme file. Real data only; any sample data labelled as sample.

---

## 5b. What the evidence map and ICP research said (reference only)

The original whiteboard parameters were: funding stage (Series A-C), SaaS (must-be), preparing for audit, employee count 50-200, number and size of clients, industry, cloud stack. After discussion, only funding stage, SaaS and audit preparation became gates. The rest are information only.

Size figures found in research conflicted (Sprinto's own review says 500-10,000 employees; most third parties say under 100 to 200; the whiteboard says 50-200). This is why employee count is not a gate.

---

## 6. Open items (ask the owner, do not guess)

1. The news source: DECIDED on 19 November 2026, Google News only (see 4.3). No official API is needed.
2. The shortlist: how it picks and how many it keeps (top 15 was proposed, not confirmed). Whether it may look up a company's website by name and keep it only if the site matches and the source is saved.
3. The manual conversion / reply rate baseline: not in the case. Ask the owner.
4. The freshness window for funding news: not set.
5. The contact, pain-point and email scoring tables above come from the supplied rubric PDF. The owner has not confirmed each number separately.
6. The Gmail refresh token expiry (Testing mode) and what is needed to keep it working through the demo.
7. Review-desk colours: not decided.
8. The pipeline code in pipeline/pipeline.py has never been run. Treat it as a draft.

---
