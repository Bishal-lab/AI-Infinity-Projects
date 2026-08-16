# Troubleshooting

## Structured Output Parser errors on "Generate SQL"

Some newer n8n versions throw errors from the **Structured Output Parser** node attached to the
`Generate SQL` Basic LLM Chain. If you hit this:

1. Remove the Structured Output Parser node.
2. On `Generate SQL`, turn **off** "Require Specific Output Format".
3. Add an **Information Extractor** node immediately after `Generate SQL`, with:
   - **Text**: `{{ $json.text }}` (the raw output of `Generate SQL`)
   - **Schema Type**: Generate From JSON Example
   - **JSON Example**: `{"sql": "SELECT * FROM sales_marketing LIMIT 5;"}`
   - Attach a Chat Model to it (same as the other nodes).
4. Downstream nodes that reference `$('Generate SQL').item.json.output.sql` now need to reference
   `$('Information Extractor').item.json.output.sql` instead — update the expressions in `SQL
   Agent`, `Chart Reqd?`, and `Chart Config`.

## AI Agent isn't calling the Postgres tool

- Confirm the Postgres Tool node ("Execute a SQL query in Postgres") is connected to `SQL Agent`
  via the **Tool** connector (not the main flow) — it should show as a small plug icon under the
  Agent node, not a normal arrow into it.
- Confirm **Tool Description** is set to "Set Automatically" so the LLM has enough context to
  decide when to invoke it.
- Try a more directive agent prompt if the model is reluctant to call tools, e.g. adding "You
  MUST call the PostgreSQL tool before answering" to the system text.

## IF node's YES/NO check isn't routing correctly

- The classifier model occasionally returns extra text around "YES"/"NO" (e.g. "YES, because...").
  The IF condition uses a **"contains"** string operator against `{{ $json.text }}` specifically
  so this still matches — if you changed the operator to "equals", switch it back to "contains".
- If the model responds in lowercase ("yes"), either add a lowercase check or add
  `"Output format: Return exactly one of the following responses - YES or NO"` more forcefully to
  the prompt (already present, but stricter models/temperature=0 help).

## QuickChart URL comes back broken / renders a blank chart

- The `Code in JavaScript1` node only encodes single quotes (`'` → `%27`) and spaces (` ` →
  `%20`). If your chart labels contain other special characters (commas inside a label, `&`,
  non-ASCII text), extend `updateChartUrl()` to encode those too, or switch the `Chart URL` prompt
  to instruct proper JSON (`"label"` with double quotes) and use QuickChart's `POST
  https://quickchart.io/chart/create` JSON endpoint instead of the `GET ?c=` query-string form —
  more robust for complex configs.
- Very long chart URLs (many categories) can exceed browser/webhook URL length limits — if a
  question returns dozens of rows, consider capping `labels_array`/`data_array` to the top N in
  the `Chart Config` prompt.

## Chart image doesn't render inline in the chat panel

- n8n's Test Chat panel renders Markdown image syntax `![chart](url)` — confirm
  `Code in JavaScript1` actually wrapped the URL in that syntax (it does, in the code provided) and
  that `Respond to Chat`'s message expression references `{{ $json.updatedChartUrl }}`, not the
  raw `Chart URL` output.

## Node types show as "unrecognized" after importing the workflow JSON

The importable workflow file
[`../n8n/Sales_Marketing_Insight_Pipeline.json`](../n8n/Sales_Marketing_Insight_Pipeline.json) was
hand-authored to n8n's documented export schema without access to a live n8n instance to verify
against. n8n's LangChain/AI node type identifiers and version numbers (`typeVersion`) change
between releases. If import flags a node:

1. Note which node(s) failed and their intended type from the table in the main
   [`README.md`](../README.md#the-pipeline-node-by-node).
2. Delete the flagged node and re-add the equivalent node from n8n's node panel by name (e.g.
   "Basic LLM Chain", "AI Agent", "Postgres Tool", "Respond to Chat").
3. Copy the prompt/settings from the corresponding file in
   [`../n8n/prompts/`](../n8n/prompts/) — every prompt is written to be pasted directly into the
   node's "Prompt"/"Text" field.
4. Reconnect it into the graph following the architecture diagram in the README.

## Credentials not carrying over

Workflow JSON exports never include credential secrets (by design). After import, every
`OpenAI Chat Model*` node and the `Execute a SQL query in Postgres` tool node will need their
credential re-selected from your own n8n credential store — see README Step 2.3.
