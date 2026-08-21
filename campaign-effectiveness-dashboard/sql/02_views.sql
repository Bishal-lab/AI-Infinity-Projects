-- ---------------------------------------------------------------------------
-- Reporting views.
--
-- Every page queries these rather than the fact tables directly, so the
-- support-status and unit rules are enforced in one place.
-- ---------------------------------------------------------------------------

-- The single funnel shape the whole dashboard reads. Rolled-up recipient rows
-- and directly-loaded aggregate rows arrive here as the same thing, tagged with
-- the support status and unit that decide whether a value may be divided.
CREATE OR REPLACE VIEW v_funnel AS
SELECT
    m.campaign_id,
    c.campaign_name,
    c.programme,
    c.objective,
    m.channel,
    ch.display_name        AS channel_display,
    ch.sort_order          AS channel_sort,
    m.stage,
    m.stage_ordinal,
    m.period_start,
    m.segment_key,
    m.derived_from,
    m.metric_value,
    m.measure_unit,
    m.aggregation,
    -- The support status stored on the row wins: it records what was true for
    -- THIS campaign's export (a column the platform supports but this file
    -- omitted lands as not_measured), falling back to the channel default.
    COALESCE(m.support_status, s.support_status) AS support_status,
    s.native_name,
    s.caveat
FROM fact_stage_metric m
JOIN dim_channel_stage_support s
  ON s.channel = m.channel AND s.stage = m.stage
LEFT JOIN dim_channel ch ON ch.channel = m.channel
LEFT JOIN dim_campaign c ON c.campaign_id = m.campaign_id;

-- Person-countable funnel values only. This is the view that funnel arithmetic
-- must use: it drops event-unit rows (Viva reactions) and anything not actually
-- measured, so no rate can ever be computed over a denominator that is not a
-- count of people.
CREATE OR REPLACE VIEW v_funnel_persons AS
SELECT *
FROM v_funnel
WHERE support_status = 'measured'
  AND measure_unit = 'persons';

-- One row per campaign x channel x stage, combined over periods, segments and
-- source objects according to each row's aggregation policy.
--
-- Viva Engage is why the policy exists. Its export repeats the community's
-- audience size on every post, and reports unique viewers per post. Adding
-- those up would count the same people once per post and claim a reach several
-- times the size of the workforce.
CREATE OR REPLACE VIEW v_stage_totals AS
SELECT
    campaign_id,
    channel,
    stage,
    stage_ordinal,
    measure_unit,
    support_status,
    aggregation,
    CASE WHEN aggregation = 'max'
         THEN MAX(metric_value)
         ELSE SUM(metric_value) END AS metric_value
FROM v_funnel
GROUP BY campaign_id, channel, stage, stage_ordinal, measure_unit,
         support_status, aggregation;

-- Wide, one row per campaign x channel, with a NULL (not a zero) wherever the
-- channel does not measure the stage.
CREATE OR REPLACE VIEW v_channel_stage_wide AS
SELECT
    campaign_id,
    channel,
    MAX(CASE WHEN stage = 'TARGETED'  AND support_status = 'measured'
             AND measure_unit = 'persons' THEN metric_value END) AS targeted,
    MAX(CASE WHEN stage = 'DELIVERED' AND support_status = 'measured'
             AND measure_unit = 'persons' THEN metric_value END) AS delivered,
    MAX(CASE WHEN stage = 'OPENED'    AND support_status = 'measured'
             AND measure_unit = 'persons' THEN metric_value END) AS opened,
    MAX(CASE WHEN stage = 'ENGAGED'   AND support_status = 'measured'
             AND measure_unit = 'persons' THEN metric_value END) AS engaged,
    MAX(CASE WHEN stage = 'COMPLETED' AND support_status = 'measured'
             AND measure_unit = 'persons' THEN metric_value END) AS completed,
    -- Kept separately: event-unit engagement is real, but it is not people and
    -- must never become a numerator over an audience size.
    MAX(CASE WHEN stage = 'ENGAGED'   AND support_status = 'measured'
             AND measure_unit = 'events'  THEN metric_value END) AS engagement_events
FROM v_stage_totals
GROUP BY campaign_id, channel;

-- Detractors and channel-specific extras, from the recipient grain.
CREATE OR REPLACE VIEW v_channel_extras AS
SELECT
    campaign_id,
    channel,
    COUNT(*)                                          AS recipients,
    COUNT(*) FILTER (WHERE bounced IS TRUE)           AS bounced,
    COUNT(*) FILTER (WHERE opted_out IS TRUE)         AS opted_out,
    COUNT(*) FILTER (WHERE assessment_passed IS TRUE) AS assessment_passed,
    AVG(score)                                        AS avg_score,
    AVG(progress_pct)                                 AS avg_progress,
    AVG(attendance_minutes)                           AS avg_attendance_minutes,
    AVG(CASE WHEN session_minutes > 0
             THEN attendance_minutes / session_minutes END) AS avg_dwell_fraction,
    COUNT(*) FILTER (WHERE opened IS TRUE AND engaged IS NOT TRUE) AS registered_no_show,
    MEDIAN(date_diff('hour', targeted_at, engaged_at))
        FILTER (WHERE targeted_at IS NOT NULL AND engaged_at IS NOT NULL)
                                                      AS median_hours_to_engage
FROM fact_recipient_stage
GROUP BY campaign_id, channel;

-- Recipient identifier -> employee, resolved at query time.
--
-- One identifier legitimately appears under several id_types (a person's work
-- email and their UPN are usually the same string), so collapse to one row per
-- hash. An identifier that points at two different employees is a roster
-- problem: it is left unresolved rather than attributed to a guess.
CREATE OR REPLACE VIEW v_recipient_identity AS
SELECT
    id_hash AS recipient_key,
    ANY_VALUE(employee_key) AS employee_key
FROM dim_employee_identity
GROUP BY id_hash
HAVING COUNT(DISTINCT employee_key) = 1;

-- Cross-channel coverage at the person grain. Only rows whose identity resolved
-- to an employee can contribute -- unresolved recipients are counted separately
-- so the UI can be honest about how much of the workforce it can actually see.
CREATE OR REPLACE VIEW v_employee_reach AS
SELECT
    r.campaign_id,
    ri.employee_key,
    e.department,
    e.region,
    e.grade_band,
    e.site,
    COUNT(DISTINCT r.channel)                                    AS channels_targeted,
    COUNT(DISTINCT CASE WHEN r.opened IS TRUE THEN r.channel END) AS channels_opened,
    MAX(r.max_stage_ordinal)                                     AS max_stage_ordinal,
    BOOL_OR(r.opened IS TRUE)                                    AS reached,
    BOOL_OR(r.engaged IS TRUE)                                   AS engaged,
    BOOL_OR(r.completed IS TRUE)                                 AS completed
FROM fact_recipient_stage r
JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
LEFT JOIN dim_employee e ON e.employee_key = ri.employee_key
GROUP BY ALL;

-- How well identity resolution is working, per campaign and channel. The dedupe
-- toggle in the UI is enabled or disabled on the strength of this.
CREATE OR REPLACE VIEW v_identity_resolution AS
SELECT
    r.campaign_id,
    r.channel,
    COUNT(*)                                             AS recipients,
    COUNT(ri.employee_key)                               AS resolved,
    CASE WHEN COUNT(*) > 0
         THEN COUNT(ri.employee_key)::DOUBLE / COUNT(*) END AS resolution_rate
FROM fact_recipient_stage r
LEFT JOIN v_recipient_identity ri ON ri.recipient_key = r.recipient_key
GROUP BY r.campaign_id, r.channel;

-- Data freshness and load health for the admin banner.
CREATE OR REPLACE VIEW v_data_quality AS
SELECT
    MAX(finished_at)                                            AS last_load_at,
    COUNT(*)                                                    AS total_batches,
    COUNT(*) FILTER (WHERE status = 'loaded')                   AS batches_clean,
    COUNT(*) FILTER (WHERE status = 'loaded_with_warnings')     AS batches_with_warnings,
    COUNT(*) FILTER (WHERE status = 'rejected')                 AS batches_rejected,
    COUNT(*) FILTER (WHERE status = 'duplicate')                AS batches_duplicate
FROM load_batch;
