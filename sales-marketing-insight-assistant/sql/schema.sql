-- Sales & Marketing Insight Assistant — Supabase/PostgreSQL schema
--
-- Run this in the Supabase SQL Editor (Project > SQL Editor > New query) before seed_data.sql.
-- Matches the column set the "Generate SQL" LLM prompt is told to expect
-- (see ../n8n/prompts/01_generate_sql.md).

CREATE TABLE IF NOT EXISTS sales_marketing (
    campaign_id     TEXT PRIMARY KEY,
    campaign_name   TEXT NOT NULL,
    campaign_type   TEXT NOT NULL,   -- Email, Social Media, Search Ads, Influencer
    region          TEXT NOT NULL,   -- North, South, East, West
    channel         TEXT NOT NULL,   -- Email, Facebook, Instagram, Google, LinkedIn, TikTok
    month           DATE NOT NULL,   -- first day of the month, e.g. 2025-01-01
    spend           NUMERIC(12, 2) NOT NULL,
    leads           INTEGER NOT NULL,
    conversions     INTEGER NOT NULL,
    sales_revenue   NUMERIC(12, 2) NOT NULL,
    cac             NUMERIC(12, 2) NOT NULL,  -- spend / NULLIF(conversions, 0)
    roas            NUMERIC(8, 3) NOT NULL    -- sales_revenue / NULLIF(spend, 0)
);

CREATE INDEX IF NOT EXISTS idx_sales_marketing_region ON sales_marketing (region);
CREATE INDEX IF NOT EXISTS idx_sales_marketing_month ON sales_marketing (month);
CREATE INDEX IF NOT EXISTS idx_sales_marketing_campaign_name ON sales_marketing (campaign_name);

COMMENT ON TABLE sales_marketing IS
  'Monthly campaign-level sales & marketing performance data used by the n8n conversational analytics assistant.';
