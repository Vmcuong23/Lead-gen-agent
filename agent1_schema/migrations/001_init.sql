-- =============================================================================
-- FDI Agent — Core Schema (v1)
-- =============================================================================
-- Design principles:
--   1. Every fact has a source + observed_at. Provenance is non-negotiable
--      because we'll need to defend "where did this come from?" for PDPL.
--   2. Companies and people both have lifecycle status (active/dormant/deleted)
--      so we can honor erasure requests without hard-deleting source records.
--   3. Fuzzy matching uses pgvector embeddings on names — Vietnamese company
--      names have many variants ("Samsung SDI Vietnam Co., Ltd",
--      "Samsung SDI VN", "SSDV") and we need entity resolution.
--   4. Events are first-class — a "factory groundbreaking" or "person changed
--      role" is a row in `events`, queryable independently of who/what it's about.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";        -- trigram similarity for names
CREATE EXTENSION IF NOT EXISTS "vector";          -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS "btree_gin";       -- composite GIN indexes

-- -----------------------------------------------------------------------------
-- sources: every external place we pull data from
-- -----------------------------------------------------------------------------
CREATE TABLE sources (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,         -- 'fia_mpi', 'vsip_tenant_list'
    name            TEXT NOT NULL,                 -- human-readable
    kind            TEXT NOT NULL,                 -- 'registry'|'industrial_park'|'news'|'serp'|'conference'|'press_release'
    base_url        TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------------------------
-- raw_documents: every page/PDF we fetch, kept for re-parsing
-- -----------------------------------------------------------------------------
CREATE TABLE raw_documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    url             TEXT NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash    TEXT NOT NULL,                 -- sha256, used for dedup
    content_type    TEXT,                          -- 'text/html', 'application/pdf'
    storage_path    TEXT,                          -- local/S3 path to raw blob
    parsed          BOOLEAN NOT NULL DEFAULT FALSE,
    parse_error     TEXT,
    UNIQUE (source_id, content_hash)
);

CREATE INDEX idx_raw_documents_url ON raw_documents (url);
CREATE INDEX idx_raw_documents_unparsed ON raw_documents (source_id) WHERE parsed = FALSE;

-- -----------------------------------------------------------------------------
-- companies: FDI companies operating in Vietnam
-- -----------------------------------------------------------------------------
CREATE TABLE companies (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Names: legal name + display name + all known variants
    legal_name      TEXT NOT NULL,                 -- as registered, e.g. "Samsung SDI Vietnam Co., Ltd"
    display_name    TEXT NOT NULL,                 -- short, e.g. "Samsung SDI Vietnam"
    name_variants   TEXT[] NOT NULL DEFAULT '{}',  -- ['SSDV', 'Samsung SDI VN', ...]
    name_embedding  vector(1024),                  -- for fuzzy matching across variants

    -- Identifiers
    tax_code        TEXT UNIQUE,                   -- mã số thuế — best canonical ID
    erc_number      TEXT,                          -- enterprise registration certificate
    investment_cert TEXT,                          -- foreign investment certificate

    -- Geography
    hq_country      TEXT,                          -- parent HQ ('KR', 'JP', 'DE')
    vn_address      TEXT,
    vn_province     TEXT,                          -- 'Bắc Ninh', 'Bình Dương', etc.
    vn_lat          NUMERIC(9,6),
    vn_lng          NUMERIC(9,6),
    industrial_park TEXT,                          -- 'VSIP Bắc Ninh', 'DEEP C Hải Phòng'

    -- Business profile
    industry_codes  TEXT[] NOT NULL DEFAULT '{}',  -- VSIC codes
    industry_label  TEXT,                          -- 'Electronics manufacturing'
    investment_usd  NUMERIC(15,2),                 -- registered capital, USD
    employee_count  INTEGER,
    factory_count   INTEGER,
    first_licensed  DATE,                          -- when did they get FDI license

    -- Web/digital footprint
    website         TEXT,
    domains         TEXT[] NOT NULL DEFAULT '{}',  -- ['samsung-sdi.com.vn', 'samsungsdi.com']
    linkedin_url    TEXT,
    facebook_url    TEXT,
    tech_stack      JSONB NOT NULL DEFAULT '{}',   -- {sap_s4hana: true, salesforce: false, ...}

    -- Lifecycle
    status          TEXT NOT NULL DEFAULT 'active' -- 'active'|'dormant'|'closed'
                    CHECK (status IN ('active','dormant','closed')),
    target_priority SMALLINT NOT NULL DEFAULT 0,   -- 0-100, set by ICP scorer

    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_enriched   TIMESTAMPTZ
);

CREATE INDEX idx_companies_legal_name_trgm  ON companies USING GIN (legal_name gin_trgm_ops);
CREATE INDEX idx_companies_display_name_trgm ON companies USING GIN (display_name gin_trgm_ops);
CREATE INDEX idx_companies_tax_code         ON companies (tax_code) WHERE tax_code IS NOT NULL;
CREATE INDEX idx_companies_province         ON companies (vn_province);
CREATE INDEX idx_companies_hq_country       ON companies (hq_country);
CREATE INDEX idx_companies_priority         ON companies (target_priority DESC) WHERE status = 'active';
CREATE INDEX idx_companies_name_embedding   ON companies USING hnsw (name_embedding vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- people: IT decision-makers at target companies
-- -----------------------------------------------------------------------------
-- PDPL note: every column here is data we obtained from a public source.
-- `purpose` records why we hold it. `deleted_at` supports erasure-on-request
-- without losing the audit trail of source documents.
-- -----------------------------------------------------------------------------
CREATE TABLE people (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID REFERENCES companies(id) ON DELETE SET NULL,

    -- Identity
    full_name       TEXT NOT NULL,
    given_name      TEXT,
    family_name     TEXT,
    name_embedding  vector(1024),

    -- Role
    title           TEXT NOT NULL,                 -- 'Head of IT', 'CIO', 'SAP Manager'
    role_category   TEXT NOT NULL                  -- normalized for filtering
                    CHECK (role_category IN (
                        'cio','cdo','cto','head_of_it','it_director',
                        'head_of_sap','erp_manager','it_manager','other'
                    )),
    seniority       TEXT                           -- 'c_level'|'vp'|'director'|'manager'|'individual'
                    CHECK (seniority IN ('c_level','vp','director','manager','individual')),
    reports_to_id   UUID REFERENCES people(id),

    -- Contact (optional, populated by Agent 3 verification step)
    email           TEXT,
    email_status    TEXT                           -- 'unverified'|'pattern_inferred'|'mx_valid'|'smtp_verified'|'invalid'|'catch_all'
                    CHECK (email_status IN (
                        'unverified','pattern_inferred','mx_valid',
                        'smtp_verified','invalid','catch_all'
                    )),
    email_confidence SMALLINT,                     -- 0-100
    phone           TEXT,                          -- only if from public source (press release etc)

    -- Public profiles
    linkedin_url    TEXT UNIQUE,
    other_profiles  JSONB NOT NULL DEFAULT '{}',   -- {twitter, github, ...}

    -- PDPL compliance
    purpose         TEXT NOT NULL                  -- why we collected this — required for PDPL
                    DEFAULT 'b2b_sales_research:it_decision_maker_outreach',
    legal_basis     TEXT NOT NULL DEFAULT 'public_source',
    consent_at      TIMESTAMPTZ,                   -- if/when we got opt-in
    deleted_at      TIMESTAMPTZ,                   -- soft delete on erasure request

    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified   TIMESTAMPTZ                    -- last time we re-checked this person still holds this role
);

CREATE INDEX idx_people_company           ON people (company_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_people_role_category     ON people (role_category) WHERE deleted_at IS NULL;
CREATE INDEX idx_people_full_name_trgm    ON people USING GIN (full_name gin_trgm_ops);
CREATE INDEX idx_people_email             ON people (email) WHERE email IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_people_linkedin          ON people (linkedin_url) WHERE linkedin_url IS NOT NULL;
CREATE INDEX idx_people_name_embedding    ON people USING hnsw (name_embedding vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- events: time-stamped facts about companies and people
-- -----------------------------------------------------------------------------
-- Examples:
--   - 'investment_announced'  (company)  — Samsung announces $1B factory
--   - 'factory_groundbreak'   (company)
--   - 'license_granted'       (company)
--   - 'role_started'          (person)   — became CIO of X on date Y
--   - 'role_ended'            (person)
--   - 'spoke_at_conference'   (person)
--   - 'quoted_in_press'       (person)
-- -----------------------------------------------------------------------------
CREATE TABLE events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind            TEXT NOT NULL,
    company_id      UUID REFERENCES companies(id) ON DELETE CASCADE,
    person_id       UUID REFERENCES people(id) ON DELETE CASCADE,
    occurred_on     DATE,                          -- when the event happened
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL DEFAULT '{}',   -- flexible details
    summary         TEXT,                          -- one-line human summary
    CONSTRAINT events_has_subject CHECK (
        company_id IS NOT NULL OR person_id IS NOT NULL
    )
);

CREATE INDEX idx_events_company    ON events (company_id, occurred_on DESC);
CREATE INDEX idx_events_person     ON events (person_id, occurred_on DESC);
CREATE INDEX idx_events_kind       ON events (kind, occurred_on DESC);

-- -----------------------------------------------------------------------------
-- evidence: which raw_document supports which fact
-- -----------------------------------------------------------------------------
-- A many-to-many link letting us say "this company's investment_usd value
-- came from FIA bulletin doc abc + VnExpress article doc xyz". Critical for
-- conflict resolution when sources disagree, and for PDPL audit trail.
-- -----------------------------------------------------------------------------
CREATE TABLE evidence (
    id              BIGSERIAL PRIMARY KEY,
    raw_document_id UUID NOT NULL REFERENCES raw_documents(id),
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('company','person','event')),
    entity_id       UUID NOT NULL,
    field_name      TEXT,                          -- which column did this support
    excerpt         TEXT,                          -- the relevant snippet
    confidence      SMALLINT NOT NULL DEFAULT 50,  -- 0-100
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_evidence_entity ON evidence (entity_type, entity_id);
CREATE INDEX idx_evidence_doc    ON evidence (raw_document_id);

-- -----------------------------------------------------------------------------
-- people_signals: hiring signals, intent data
-- -----------------------------------------------------------------------------
-- E.g. company posted a "SAP S/4HANA Consultant" job — this is intent.
-- Kept separate from events because it's volume + lower confidence per row.
-- -----------------------------------------------------------------------------
CREATE TABLE company_signals (
    id              BIGSERIAL PRIMARY KEY,
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    signal_type     TEXT NOT NULL,                 -- 'job_posting'|'tech_adoption'|'expansion_announced'
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB NOT NULL DEFAULT '{}',
    score           SMALLINT NOT NULL DEFAULT 50,  -- 0-100 strength
    expires_at      TIMESTAMPTZ                    -- signals decay
);

-- Partial index predicates must be IMMUTABLE, so we filter only on
-- the constant "no expiry" case; the planner still uses this for the
-- common active-signal lookup, and queries that need "not yet expired"
-- can layer `expires_at > NOW()` on top.
CREATE INDEX idx_company_signals_active ON company_signals (company_id, signal_type)
    WHERE expires_at IS NULL;
CREATE INDEX idx_company_signals_expiry ON company_signals (expires_at)
    WHERE expires_at IS NOT NULL;

-- -----------------------------------------------------------------------------
-- jobs: agent task queue
-- -----------------------------------------------------------------------------
-- Used by all three agents to coordinate work. A job is a unit like
-- "scrape FIA bulletin for Q3 2025" or "find IT director at company X".
-- -----------------------------------------------------------------------------
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent           TEXT NOT NULL,                 -- 'agent2_fdi'|'agent3_people'
    kind            TEXT NOT NULL,                 -- 'scrape_fia'|'enrich_company'|'find_people'|...
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','done','failed','retrying')),
    priority        SMALLINT NOT NULL DEFAULT 50,
    payload         JSONB NOT NULL DEFAULT '{}',
    result          JSONB,
    error           TEXT,
    attempts        SMALLINT NOT NULL DEFAULT 0,
    max_attempts    SMALLINT NOT NULL DEFAULT 3,
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_jobs_dispatch ON jobs (agent, status, scheduled_for, priority DESC)
    WHERE status IN ('pending', 'retrying');

-- -----------------------------------------------------------------------------
-- updated_at triggers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_companies_touch BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_people_touch BEFORE UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- -----------------------------------------------------------------------------
-- Seed sources
-- -----------------------------------------------------------------------------
INSERT INTO sources (slug, name, kind, base_url) VALUES
    ('fia_mpi',           'Foreign Investment Agency (MPI)',  'registry',         'https://fia.mpi.gov.vn'),
    ('gso',               'General Statistics Office',         'registry',         'https://www.gso.gov.vn'),
    ('npr',               'National Business Registration',    'registry',         'https://dangkykinhdoanh.gov.vn'),
    ('vsip',              'VSIP industrial parks',             'industrial_park',  'https://vsip.com.vn'),
    ('becamex',           'Becamex IDC',                       'industrial_park',  'https://becamex.com.vn'),
    ('deepc',             'DEEP C Industrial Zones',           'industrial_park',  'https://deepc.vn'),
    ('amata',             'Amata Vietnam',                     'industrial_park',  'https://amata.com.vn'),
    ('vir',               'Vietnam Investment Review',         'news',             'https://vir.com.vn'),
    ('vneconomy',         'VnEconomy',                         'news',             'https://vneconomy.vn'),
    ('vnexpress_business','VnExpress Business',                'news',             'https://vnexpress.net/kinh-doanh'),
    ('brave_search',      'Brave Search API',                  'serp',             'https://api.search.brave.com'),
    ('serpapi',           'SerpAPI',                           'serp',             'https://serpapi.com'),
    ('vncio_summit',      'Vietnam CIO Summit',                'conference',       NULL),
    ('sap_now_vn',        'SAP NOW Vietnam',                   'conference',       NULL),
    ('fpt_techday',       'FPT Techday',                       'conference',       NULL)
ON CONFLICT (slug) DO NOTHING;
