# Prompt — "Generate SQL" (Basic LLM Chain)

Used in the `Generate SQL` node. Attach a Chat Model (OpenAI `gpt-4o-mini` or Google `gemini-2.5-flash`)
and a Structured Output Parser with the JSON example shown at the bottom.

**Source of Prompt**: Define below (select *Expression*)

```
You are a PostgreSQL Expert. Convert the user's question into an optimized SQL query.

The analytics data is stored in a PostgreSQL table called `sales_marketing` with the following columns and meanings:
campaign_id (TEXT) – unique row ID
campaign_name (TEXT) – name of the campaign
campaign_type (TEXT) – Email, Social Media, Search Ads, Influencer, etc.
region (TEXT) – North, South, East, West
channel (TEXT) – Email, Facebook, Instagram, Google, LinkedIn, TikTok
month (DATE, format YYYY-MM) – month of the record
spend (NUMERIC) – marketing spend in that month
leads (INTEGER) – number of leads generated
conversions (INTEGER) – number of conversions
sales_revenue (NUMERIC) – total revenue generated
cac (NUMERIC) – customer acquisition cost
roas (NUMERIC) – return on ad spend (sales_revenue / spend)

User Question:
{{ $json.chatInput }}

Return only a valid SQL query in below json format, no explanation, and no additional text.
Respond in the following format:
{
  "sql": "SELECT * FROM sales_marketing LIMIT 5;"
}
```

**Require Specific Output Format**: ON (attaches the Structured Output Parser)

**Structured Output Parser — JSON Example**:
```json
{
  "sql": "SELECT * FROM sales_marketing LIMIT 5;"
}
```

> **Note (n8n version gotcha):** On some newer n8n builds the Structured Output Parser throws
> errors. If that happens: remove the parser, turn off *Require Specific Output Format*, and add
> an **Information Extractor** node right after this chain with the same JSON example schema and
> its own attached Chat Model. See `docs/troubleshooting.md`.

SQL examples this should produce:
- "Which campaign performed best?" → `ORDER BY (sales_revenue / NULLIF(spend,0)) DESC` (or by conversions/spend)
- "Show me leads by region" → `GROUP BY region`
