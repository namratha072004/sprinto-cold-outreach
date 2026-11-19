-- Sprinto outreach: Neon Postgres schema. Safe to re-run (every statement is IF NOT EXISTS).
-- Nothing in this schema or the app can send email: drafts are created in Gmail, review only records a decision.

-- ============================== RUBRIC (versioned; code reads the ACTIVE version, no value is hardcoded) ==============================
CREATE TABLE IF NOT EXISTS rubric_versions (
  id           serial PRIMARY KEY,
  version      text NOT NULL UNIQUE,
  source       text NOT NULL,                    -- e.g. 'Sprinto_Agent_Complete_Rubric.pdf + rubric.py'
  is_active    boolean NOT NULL DEFAULT false,
  notes        text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_rubric ON rubric_versions ((true)) WHERE is_active;

CREATE TABLE IF NOT EXISTS rubric_dimensions (   -- the five scored questions
  id                 serial PRIMARY KEY,
  rubric_version_id  int NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  key                text NOT NULL,              -- business_fit, customer_trust, ...
  label              text NOT NULL,
  weight             int  NOT NULL,              -- points when level = max_level
  max_level          int  NOT NULL DEFAULT 2,
  sort_order         int  NOT NULL DEFAULT 0,
  UNIQUE (rubric_version_id, key)
);
CREATE TABLE IF NOT EXISTS rubric_levels (       -- what each level means, shown to the AI and to the reviewer
  id            serial PRIMARY KEY,
  dimension_id  int NOT NULL REFERENCES rubric_dimensions(id) ON DELETE CASCADE,
  level         int NOT NULL,
  description   text NOT NULL,
  UNIQUE (dimension_id, level)
);
CREATE TABLE IF NOT EXISTS rubric_gates (        -- all gates are required
  id                 serial PRIMARY KEY,
  rubric_version_id  int NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  key                text NOT NULL,              -- saas, series, compliance, score
  label              text NOT NULL,
  kind               text NOT NULL CHECK (kind IN ('level_min', 'stage_in', 'total_min')),
  params             jsonb NOT NULL,             -- {"dimension":"business_fit","min":2} | {"stages":["series a",...]} | {"min":65}
  sort_order         int NOT NULL DEFAULT 0,
  UNIQUE (rubric_version_id, key)
);
CREATE TABLE IF NOT EXISTS rubric_thresholds (
  rubric_version_id  int NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  key                text NOT NULL,              -- contact_pass_total, pain_pass_total, email_pass_total, ...
  value              numeric NOT NULL,
  PRIMARY KEY (rubric_version_id, key)
);
CREATE TABLE IF NOT EXISTS rubric_limits (       -- page budget, rounds, retries, Hunter cap, revisions
  rubric_version_id  int NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  key                text NOT NULL,
  value              int NOT NULL,
  PRIMARY KEY (rubric_version_id, key)
);
CREATE TABLE IF NOT EXISTS rubric_capabilities ( -- the only Sprinto capabilities an email may mention
  rubric_version_id  int NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  name               text NOT NULL,
  PRIMARY KEY (rubric_version_id, name)
);
CREATE TABLE IF NOT EXISTS rubric_rules (        -- contact criteria, role order, email rules, search-query templates, prompts
  rubric_version_id  int NOT NULL REFERENCES rubric_versions(id) ON DELETE CASCADE,
  area               text NOT NULL,              -- search | contact | pain | email | status
  key                text NOT NULL,
  value              jsonb NOT NULL,
  PRIMARY KEY (rubric_version_id, area, key)
);

-- ============================== RUNS ==============================
CREATE TABLE IF NOT EXISTS runs (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  status             text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'DONE', 'FAILED', 'STOPPED')),
  rubric_version_id  int  NOT NULL REFERENCES rubric_versions(id),
  started_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz,
  hunter_calls       int NOT NULL DEFAULT 0,
  hunter_credits     numeric NOT NULL DEFAULT 0,
  hunter_paused      text,                       -- reason, if lookups were paused
  params             jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS discovery_results (   -- one row per Google News result found
  id                 bigserial PRIMARY KEY,
  run_id             uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  query              text NOT NULL,
  headline           text NOT NULL,
  source_name        text,
  source_url         text NOT NULL,
  published_at       date,
  company_name       text,
  company_domain     text,                       -- NULL unless confirmed from a source. Never guessed.
  domain_status      text NOT NULL DEFAULT 'unknown' CHECK (domain_status IN ('confirmed', 'unknown')),
  domain_evidence    text,                       -- URL or excerpt that confirms the domain
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS shortlist (
  id                    bigserial PRIMARY KEY,
  run_id                uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  discovery_result_id   bigint REFERENCES discovery_results(id),
  company_name          text NOT NULL,
  domain                text,
  rank                  int  NOT NULL,
  reason                text NOT NULL,           -- why it was kept, in plain words
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, company_name)
);

-- ============================== PER COMPANY ==============================
CREATE TABLE IF NOT EXISTS companies (
  id                  bigserial PRIMARY KEY,
  run_id              uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  domain              text NOT NULL,
  name                text,
  status              text NOT NULL DEFAULT 'QUEUED' CHECK (status IN
                       ('QUEUED','WORKING','QUALIFIED','DISQUALIFIED','LOW_PRIORITY','INSUFFICIENT_EVIDENCE','NO_SUITABLE_CONTACT',
                        'INSUFFICIENT_PAIN_EVIDENCE','EMAIL_QA_FAILED','CONTACT_LOOKUP_PAUSED','DRAFT_READY','ERROR')),
  stage               text NOT NULL DEFAULT 'queued',   -- queued | reading_website | finding_contact | writing_email | done
  funding_stage       text,
  funding_date        date,
  total_score         numeric,
  gates               jsonb,                            -- {"saas":{"state":"passed","detail":"..."},...}
  flags               text[] NOT NULL DEFAULT '{}',
  reason              text,
  escalation_reason   text,
  research_limit_hit  boolean NOT NULL DEFAULT false,
  retry_counts        jsonb NOT NULL DEFAULT '{}',      -- page reads, model calls, Hunter calls
  usage               jsonb NOT NULL DEFAULT '{}',      -- tokens
  started_at          timestamptz,
  finished_at         timestamptz,
  seconds             numeric,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, domain)
);
CREATE TABLE IF NOT EXISTS evidence (
  id             bigserial PRIMARY KEY,
  company_id     bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  dimension      text NOT NULL,                  -- a rubric dimension key, 'funding', or 'employees'
  claim          text,
  source_kind    text NOT NULL CHECK (source_kind IN ('website', 'news')),
  source_url     text NOT NULL,
  excerpt        text NOT NULL,                  -- verbatim, checked against the page text by code
  retrieved_on   date NOT NULL,
  published_on   date,
  label          text NOT NULL CHECK (label IN ('observed', 'inferred', 'unknown', 'conflicting')),
  round_no       int
);
CREATE TABLE IF NOT EXISTS research_rounds (
  id                bigserial PRIMARY KEY,
  company_id        bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  round_no          int NOT NULL,
  gap               jsonb,
  sources           jsonb,                       -- pages and news read this round, with result and attempts
  new_evidence      jsonb,
  revised_decision  text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, round_no)
);
CREATE TABLE IF NOT EXISTS qualification_scores (
  id          bigserial PRIMARY KEY,
  company_id  bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  dimension   text NOT NULL,
  level       int  NOT NULL,
  points      numeric NOT NULL,
  state       text NOT NULL,                     -- positive | missing | explicit_negative
  reasoning   text,
  UNIQUE (company_id, dimension)
);
CREATE TABLE IF NOT EXISTS contacts (
  id               bigserial PRIMARY KEY,
  company_id       bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  name             text NOT NULL,
  position         text,
  department       text,
  seniority        text,
  hunter_confidence int,
  email            text,                         -- research data only; never a delivery destination
  role_category    text,
  scores           jsonb NOT NULL,
  total            int NOT NULL,
  passed           boolean NOT NULL,
  chosen           boolean NOT NULL DEFAULT false,
  reasoning        text,
  hunter_sources   jsonb,
  contact_correct  boolean                       -- the Growth Lead's verdict (feeds contact accuracy)
);
CREATE TABLE IF NOT EXISTS pain_hypotheses (
  id                bigserial PRIMARY KEY,
  company_id        bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  position          int NOT NULL,                -- 1 = used in the email
  observed_fact     text NOT NULL,
  evidence_url      text NOT NULL,
  evidence_excerpt  text NOT NULL,
  hypothesis        text NOT NULL,
  role_relevance    text,
  possible_consequence text,
  capability        text NOT NULL,
  scores            jsonb NOT NULL,
  total             int NOT NULL,
  passed            boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
  id                  bigserial PRIMARY KEY,
  company_id          bigint NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  contact_id          bigint REFERENCES contacts(id),
  revision            int NOT NULL DEFAULT 0,
  subject             text NOT NULL,
  body                text NOT NULL,
  words               int,
  qa                  jsonb,
  qa_total            int,
  passed              boolean NOT NULL DEFAULT false,
  is_current          boolean NOT NULL DEFAULT false,
  original_subject    text,
  original_body       text,
  gmail_draft_id      text,                      -- the ONE draft for this company; replaced, never duplicated
  gmail_draft_error   text,
  draft_created_at    timestamptz,
  draft_updated_at    timestamptz,
  review_decision     text NOT NULL DEFAULT 'pending' CHECK (review_decision IN ('pending', 'approved', 'edited', 'rejected')),
  review_note         text,
  reviewed_at         timestamptz,
  opened_at           timestamptz,
  review_seconds      numeric
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_email_per_company ON emails (company_id) WHERE is_current;

CREATE TABLE IF NOT EXISTS run_log (
  id          bigserial PRIMARY KEY,
  run_id      uuid REFERENCES runs(id) ON DELETE CASCADE,
  company_id  bigint REFERENCES companies(id) ON DELETE CASCADE,
  at          timestamptz NOT NULL DEFAULT now(),
  level       text NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warn', 'error')),
  event       text NOT NULL,
  detail      jsonb
);

-- ============================== CACHES (every Hunter result and page is kept) ==============================
CREATE TABLE IF NOT EXISTS hunter_cache (
  domain        text PRIMARY KEY,
  data          jsonb NOT NULL,
  credits_used  numeric,
  retrieved_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS page_cache (
  url           text PRIMARY KEY,
  status        text NOT NULL,
  text          text,
  links         jsonb,
  attempts      int,
  retrieved_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE companies ADD COLUMN IF NOT EXISTS assessment jsonb;      -- the full verified assessment (facts, levels, funding)
ALTER TABLE companies ADD COLUMN IF NOT EXISTS sources_text jsonb;     -- url -> text that was read; later steps verify every excerpt against it
ALTER TABLE companies ADD COLUMN IF NOT EXISTS sources_used jsonb;     -- urls read, so later steps can rebuild the page text from page_cache
CREATE INDEX IF NOT EXISTS idx_companies_run ON companies (run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_company ON evidence (company_id);
CREATE INDEX IF NOT EXISTS idx_discovery_run ON discovery_results (run_id);
CREATE INDEX IF NOT EXISTS idx_log_run ON run_log (run_id, at);

-- ============================== v2 additions (safe to re-run) ==============================
ALTER TABLE companies ALTER COLUMN domain DROP NOT NULL;                -- a shortlisted company has no verified website until the website step succeeds
ALTER TABLE companies ADD COLUMN IF NOT EXISTS shortlist_rank int;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS overruled boolean NOT NULL DEFAULT false;   -- the Growth Lead disagreed with a no-go
ALTER TABLE companies ADD COLUMN IF NOT EXISTS overrule_note text;
ALTER TABLE discovery_results ADD COLUMN IF NOT EXISTS outcome text;      -- what happened to this article (shortlisted, merged, not_funding_news, ...)
ALTER TABLE discovery_results ADD COLUMN IF NOT EXISTS outcome_note text;
ALTER TABLE discovery_results ADD COLUMN IF NOT EXISTS merged_into bigint;
ALTER TABLE discovery_results ADD COLUMN IF NOT EXISTS company_id bigint;
ALTER TABLE shortlist ADD COLUMN IF NOT EXISTS company_id bigint;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS gmail_message_id text;        -- lets the dashboard open the exact Gmail draft
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS email_verification text;    -- Hunter's verdict on the address (research data only)
ALTER TABLE emails DROP CONSTRAINT IF EXISTS emails_review_decision_check;
ALTER TABLE emails ADD CONSTRAINT emails_review_decision_check CHECK (review_decision IN ('pending', 'approved', 'edited', 'rejected', 'done'));
ALTER TABLE companies ADD COLUMN IF NOT EXISTS claimed_at timestamptz;     -- stops two browser tabs from running the same step twice
