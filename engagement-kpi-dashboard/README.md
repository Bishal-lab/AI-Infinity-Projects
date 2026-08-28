# Engagement KPI dashboard — "Flight to Success"

One self-contained HTML page that consolidates the analytics exports from five
systems and reports the ten campaign KPIs against them.

| Source | Grain | Recognised by |
| --- | --- | --- |
| Viva Engage campaign KPI | campaign × day | `Campaign`, `Community_Members`, `Active_Users` |
| Email campaign KPI | campaign × day | `Campaign`, `Delivered`, `Clicked`, `Opened` |
| WhatsApp campaign KPI | campaign × day | `Campaign`, `Delivered`, `Read` |
| LMS employee-wise report | employee | `Employee_ID`, `Modules_Assigned`, `Modules_Completed` |
| Teams webinar attendance | employee | `Employee_ID`, `Registered`, `Attended` |

Files are identified **by the columns they carry, never by filename** — an export
saved as `export (3).xlsx` still lands in the right place. Each source is
optional: whatever is missing shows the columns it is waiting for, and the KPIs
that depend on it say so rather than reading as zero.

## Running it

Open `dashboard.html` in a browser and choose or drop the `.xlsx` exports. There
is no server, no install and no build step, and the page works offline once
saved locally.

Nothing leaves the browser. The LMS, webinar and community records name
individual employees, so the page parses them in the tab — no upload, no
network call, no `localStorage`, no cookie. The `test/drive.mjs` harness asserts
this by failing if any non-font request is made while the files are loaded.

`.xlsx` is read natively: `DecompressionStream('deflate-raw')` inflates the ZIP
entries and the worksheet XML is scanned directly. No library, nothing for a
proxy to block.

## Editing it

`dashboard.html` is generated. Edit the parts in `src/` and reassemble:

```sh
./build.sh
```

| Part | What it holds |
| --- | --- |
| `src/00_page.html` | markup, design tokens, all CSS |
| `src/01_xlsx.js` | the `.xlsx` reader |
| `src/02_sources.js` | source signatures, the four reading decisions, filters |
| `src/03_kpis.js` | the ten KPIs, each carrying its own denominator |
| `src/04_charts.js` | SVG chart primitives |
| `src/05_view.js` | the four bands |
| `src/06_wiring.js` | file input, drag and drop, filter handlers |

## Tests

`test/` needs Playwright (`npm install playwright`); everything else runs on
Node 18+ with no dependencies.

| Command | Checks |
| --- | --- |
| `node test/gold.mjs` | every KPI computed headlessly from `samples/`, so the figures can be reconciled by hand |
| `node test/drive.mjs` | empty state, a single file, a renamed file, the full set, the working panel, filtering, geometry, dark mode, and that no request leaves the page |
| `node test/narrow.mjs` | no horizontal page scroll at 390 px or 820 px |
| `node test/labels.mjs` | every label drawn on a mark clears 3.5:1 against that mark, in both themes |
| `node test/print.mjs` | the print layout resolves to black on white in **both** colour schemes, controls are gone, and the active filters are on the page |
| `node test/export.mjs` | the CSV export on all three routes — local Blob, viewer capability, and capability absent |

Expected KPI values against the five sample files:

```
 1. Campaigns Running                      5   across email + WhatsApp + Viva
 2. Total Deliveries                  71,823   email + WhatsApp
 3. Email Engagement Rate              11.1%   5,738 ÷ 51,893
 4. WhatsApp Engagement Rate           23.4%   4,655 ÷ 19,930
 5. Overall Digital Engagement         14.5%   10,393 clicks ÷ 71,823
 6. Webinar Registration Rate          88.3%   53 ÷ 60
 7. Webinar Attendance Rate            79.2%   42 ÷ 53
 8. Learning Completion Rate           48.4%   235 ÷ 486 modules
 9. Viva Engagement Rate               30.0%   10,002 ÷ 33,390
10. Learning Engagement Index         64/100   all four components
```

Drop only the four real exports and KPI 4 goes blank, KPIs 1, 2 and 5 pick up a
`partial` chip, and KPI 2 reads 51,893 — which is the behaviour to check when a
source is late.

## Four readings the page commits to

Each of these has a defensible answer either way, so the page states which one
it took beside the number that depends on it — select any KPI tile.

1. **`Delivered` counts messages, not people.** Neither campaign export carries
   a recipient list, so unique reach cannot be derived. KPI 2 is therefore
   labelled *Total Deliveries*. A per-recipient send log with `Employee_ID`
   would turn it into a true `COUNT DISTINCT` — and would join to the LMS and
   webinar files, which is where it gets interesting.
2. **KPI 5 counts clicks only**, across email and WhatsApp. An email click and a
   Viva "like" are not the same act, and Viva has no `Delivered` to sit in the
   denominator. The mixed reading is reported in the tile's note rather than in
   the headline.
3. **Viva's `Community_Members` behaves like "members targeted that day"** — it
   moves by thousands between consecutive days, which a community size does not
   do. KPI 9 is read as daily activation of that targeted set.
4. **The index weights are a management choice**: learning completion 40%,
   webinar attendance 30%, assessment score 20%, certification 10%. Components
   with no data drop out and the remaining weights renormalise, so a missing
   source is never counted as a zero.

## Still open

- `02_WhatsApp_Campaign_KPI.xlsx` has not been supplied. Two KPIs are held for
  it and fill the moment it is dropped in — no code change.
- The 60 employees in the exports are the denominator for KPI 6. If they are the
  invited set rather than the whole target population, that rate is measuring
  something narrower than it appears to.

## Filters, print and export

Channel, location and learning path come from the two employee reports. The
campaign exports arrive pre-aggregated and carry no such columns, so those tiles
stay unfiltered and the page says so rather than appearing to filter. The date
range applies to the campaign exports, which are the only dated ones.

Every chart carries a **Numbers** toggle. A tooltip needs a pointer, so the
figures behind each chart are also available as a table — reachable by keyboard,
visible on touch, and easy to copy.

**Print** produces a management pack: light palette regardless of the viewer's
theme, controls stripped, cards kept whole across page breaks, and the active
filters printed on the page. A sheet of filtered numbers that does not say it
was filtered is the one way this document can mislead, so that line is not
optional.

**Download KPIs (CSV)** emits the ten tiles with their value, basis, status and
the reading applied, carrying the period and filter state in its header rows.
Saved to disk the page uses an ordinary Blob download; inside a published
artifact that route is inert, so it uses the viewer's file-save capability and
hides the button when that capability is unavailable.

## Samples

`samples/` holds the four supplied exports plus one fixture, used by every test.
The data in all of them is synthetic.

`02_WhatsApp_Campaign_KPI_SAMPLE.xlsx` **was not supplied — it is a stand-in**
written to imitate what the WhatsApp tool would export, so the two KPIs that
depend on it could be exercised before the real file arrives. Its read rates run
far above e-mail open rates, which is the behaviour that matters here. Replace it
with the genuine export and nothing needs changing: detection is by header
signature, and the real file will be claimed the same way. Its numbers are
invented and must not be reported as results.
