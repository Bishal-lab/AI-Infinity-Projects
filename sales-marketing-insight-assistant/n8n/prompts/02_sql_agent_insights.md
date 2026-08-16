# Prompt — "SQL Agent" (AI Agent node)

Rename the AI Agent node to `SQL Agent`. Attach a Chat Model, and attach a **Postgres Tool**
node ("Execute a SQL query in Postgres") as its tool — credentials built from your Supabase
connection string (host/port/user/database/password), Operation = *Execute Query*, Tool
Description = *Set Automatically*.

**Source for Prompt**: Define below (select *Expression*)

```
You are a helpful assistant. You have access to PostgreSQL Tool to execute SQL queries and get the response.

For the below User request, first retrieve the data from Database, then analyze it and generate:
- Key insights
- Summary of trends
- Anomalies or unexpected patterns
- Recommended actions
- One-sentence executive summary

User request:
{{ $('When chat message received').item.json.chatInput }}

SQL query:
{{ $('Generate SQL').item.json.output.sql }}
```

Sample outputs the agent should be capable of producing:
- "The north region is outperforming by 27% compared to the national average."
- "Campaign Gamma produced the highest ROI at 5.2x."

> **Security note:** the Postgres credential this tool uses should be a **read-only** database
> role in Supabase (`SELECT` only). The AI Agent executes whatever SQL the previous node
> generated — a read-only role is the guardrail against an errant destructive statement.
