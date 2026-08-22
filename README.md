# AI-Infinity-Projects

A collection of practical AI/automation projects.

## Projects

- [`sales-marketing-insight-assistant/`](sales-marketing-insight-assistant/) — a no-code,
  conversational analytics assistant built in n8n. Business users ask natural-language questions
  about sales & marketing data and get back generated SQL, live query results, insights,
  recommendations, and charts (OpenAI/Gemini + Supabase Postgres + QuickChart).

- [`campaign-effectiveness-dashboard/`](campaign-effectiveness-dashboard/) — an on-premises
  Streamlit dashboard for gauging how well internal communication campaigns land across Email,
  WhatsApp, a Learning Management tool, MS Teams webinars and Viva Engage. Drop each platform's
  CSV/Excel export into a folder and it normalises them into one reach-and-engagement funnel with
  management KPIs, coverage by department, and an Excel pack. Runs fully offline with no outbound
  calls, maps to your own column headers through editable YAML, and is careful to report "n/a"
  rather than zero where a platform has no such stage (Streamlit + DuckDB + Plotly).

- [`bfsi-insurance-digest-bot/`](bfsi-insurance-digest-bot/) — a daily BFSI and life-insurance
  news brief, delivered to Telegram and Gmail at 08:00 IST. It reads public RSS/Atom feeds from
  specialist BFSI desks, the RBI and targeted news queries (which is how IRDAI actions get in),
  scores every story against an editable keyword taxonomy, routes it into sections such as Life
  Insurance and Regulation & Policy, collapses the same story arriving from four sources into one
  item, and remembers what it already sent so an overlapping window never repeats itself. Runs free
  on a GitHub Actions schedule or on any machine with cron; two dependencies, no news API keys, and
  a test suite that never touches the network (Python + RSS + Telegram Bot API + Gmail SMTP).
