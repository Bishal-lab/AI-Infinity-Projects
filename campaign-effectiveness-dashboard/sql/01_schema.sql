-- ---------------------------------------------------------------------------
-- Campaign Communication Effectiveness — warehouse schema (DuckDB)
--
-- Two fact grains, because the five platforms genuinely differ:
--
--   fact_recipient_stage   one row per campaign x channel x person.
--                          Email, WhatsApp, LMS and Teams export this way.
--
--   fact_stage_metric      one row per campaign x channel x stage x period.
--                          Fed BOTH by rolling up fact_recipient_stage AND by
--                          loading aggregate-only sources (Viva Engage) directly.
--
-- Every funnel and KPI read goes through fact_stage_metric, so an
-- aggregate-only channel is a first-class citizen rather than a special case
-- smeared through the UI. fact_recipient_stage backs the deep dives, coverage
-- by department, cross-channel dedupe and time-to-engage.
-- ---------------------------------------------------------------------------

CREATE SEQUENCE IF NOT EXISTS seq_batch_id START 1;

-- --------------------------------------------------------------------------
-- Dimensions
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_campaign (
    campaign_id             TEXT PRIMARY KEY,
    campaign_name           TEXT NOT NULL,
    programme               TEXT,
    objective               TEXT,          -- awareness | change | training | compliance | engagement
    owner_team              TEXT,
    start_date              DATE,
    end_date                DATE,
    mandatory               BOOLEAN DEFAULT FALSE,
    target_population       INTEGER,       -- authoritative denominator when exports undercount
    target_completion_rate  DOUBLE,        -- per-campaign RAG override
    target_engagement_rate  DOUBLE,
    updated_at              TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_employee (
    employee_key      TEXT PRIMARY KEY,    -- hashed employee id
    department        TEXT,
    sub_department    TEXT,
    region            TEXT,
    country           TEXT,
    site              TEXT,
    grade_band        TEXT,
    employment_type   TEXT,
    is_people_manager BOOLEAN,
    is_active         BOOLEAN DEFAULT TRUE,
    batch_id          BIGINT,
    updated_at        TIMESTAMP DEFAULT now()
);

-- The join spine. Channel exports carry emails, phone numbers and LMS user ids
-- -- never a department. This table is what lets a WhatsApp number and an LMS
-- login resolve to the same person, and it is the only reason cross-channel
-- deduplication is possible at all.
CREATE TABLE IF NOT EXISTS dim_employee_identity (
    id_hash      TEXT NOT NULL,
    id_type      TEXT NOT NULL,            -- email | upn | msisdn | lms_user_id | employee_no
    employee_key TEXT NOT NULL,
    PRIMARY KEY (id_hash, id_type)
);

CREATE TABLE IF NOT EXISTS dim_channel (
    channel      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    grain        TEXT NOT NULL,            -- recipient | aggregate
    sort_order   INTEGER
);

-- Materialised from config/stage_ladder.yaml at startup so SQL and UI read one
-- source of truth. support_status is three-valued -- see comms_dashboard/models.py.
CREATE TABLE IF NOT EXISTS dim_channel_stage_support (
    channel        TEXT NOT NULL,
    stage          TEXT NOT NULL,
    stage_ordinal  INTEGER NOT NULL,
    support_status TEXT NOT NULL,          -- measured | not_applicable | not_measured
    unit           TEXT NOT NULL,          -- persons | events
    native_name    TEXT,
    caveat         TEXT,
    PRIMARY KEY (channel, stage)
);

-- --------------------------------------------------------------------------
-- Facts
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fact_recipient_stage (
    campaign_id        TEXT NOT NULL,
    channel            TEXT NOT NULL,
    recipient_key      TEXT NOT NULL,      -- hashed native identifier

    -- Note: there is deliberately no employee_key column here. Identity is
    -- resolved at query time through v_recipient_identity, so a roster loaded
    -- *after* the channel exports retroactively resolves all existing history
    -- with no re-processing step to remember to run.

    -- Stage flags. NULL means "unknown", and is never coerced to FALSE: the
    -- difference between "we know they did not open" and "we have no open data"
    -- is the difference between a real result and a fabricated one.
    targeted           BOOLEAN,
    delivered          BOOLEAN,
    opened             BOOLEAN,
    engaged            BOOLEAN,
    completed          BOOLEAN,

    targeted_at        TIMESTAMP,
    delivered_at       TIMESTAMP,
    opened_at          TIMESTAMP,
    engaged_at         TIMESTAMP,
    completed_at       TIMESTAMP,

    max_stage_ordinal  TINYINT,

    bounced            BOOLEAN,
    opted_out          BOOLEAN,
    progress_pct       DOUBLE,
    score              DOUBLE,
    assessment_passed  BOOLEAN,
    attendance_minutes DOUBLE,
    session_minutes    DOUBLE,
    interactions       INTEGER,
    department         TEXT,               -- from the export, used only when no roster is loaded

    batch_id           BIGINT,
    updated_at         TIMESTAMP DEFAULT now(),

    -- The natural key. This is what makes re-dropping an export idempotent.
    PRIMARY KEY (campaign_id, channel, recipient_key)
);

CREATE TABLE IF NOT EXISTS fact_stage_metric (
    campaign_id      TEXT NOT NULL,
    channel          TEXT NOT NULL,
    stage            TEXT NOT NULL,
    stage_ordinal    TINYINT NOT NULL,
    period_start     DATE NOT NULL,
    segment_key      TEXT NOT NULL DEFAULT 'ALL',
    source_object_id TEXT NOT NULL DEFAULT '-',  -- Viva post id; '-' for rollups
    derived_from     TEXT NOT NULL,              -- recipient_rollup | source_aggregate
    metric_value     DOUBLE,
    measure_unit     TEXT NOT NULL,              -- persons | events
    -- How to combine rows when rolling up to a campaign total. Interaction
    -- counts add up ('sum'); an audience size repeated on every post describes
    -- the same people each time and must not ('max').
    aggregation      TEXT NOT NULL DEFAULT 'sum', -- sum | max
    support_status   TEXT NOT NULL,
    batch_id         BIGINT,
    updated_at       TIMESTAMP DEFAULT now(),
    PRIMARY KEY (campaign_id, channel, stage, period_start, segment_key,
                 source_object_id, derived_from)
);

-- --------------------------------------------------------------------------
-- Operations
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS load_batch (
    batch_id             BIGINT PRIMARY KEY,
    file_name            TEXT,
    file_sha256          TEXT,
    source               TEXT,
    detection_confidence DOUBLE,
    mapping_version      TEXT,
    campaign_id          TEXT,
    rows_read            INTEGER,
    rows_loaded          INTEGER,
    rows_rejected        INTEGER,
    status               TEXT,             -- loaded | loaded_with_warnings | rejected | duplicate
    started_at           TIMESTAMP,
    finished_at          TIMESTAMP,
    superseded_by        BIGINT,
    report               JSON
);

CREATE TABLE IF NOT EXISTS load_issue (
    batch_id         BIGINT,
    severity         TEXT,
    code             TEXT,
    column_name      TEXT,
    message          TEXT,
    example_values   TEXT,
    occurrence_count INTEGER
);

-- Remembers an admin's manual answer to "which source is this file?" so the
-- same ambiguous export does not have to be classified twice.
CREATE TABLE IF NOT EXISTS manual_source_override (
    file_sha256 TEXT PRIMARY KEY,
    source      TEXT,
    campaign_id TEXT,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recipient_campaign ON fact_recipient_stage (campaign_id, channel);
CREATE INDEX IF NOT EXISTS idx_metric_campaign ON fact_stage_metric (campaign_id, channel, stage);
CREATE INDEX IF NOT EXISTS idx_metric_period ON fact_stage_metric (period_start);
