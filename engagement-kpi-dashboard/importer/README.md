# On-prem importer

Turns the five exports into a dashboard that is **already filled in** when you
open it. Nobody drags anything.

## Using it

1. Drop the exports into `inbox/`. Any number of `.xlsx`, in any order, named
   whatever your platforms call them.
2. Double-click **`run.bat`** (Windows) or run **`./run.sh`**.
3. Open the file it writes into `out/`.

That is the whole thing. It needs Python 3.9+ and `openpyxl`:

```
pip install openpyxl pyyaml
```

`pyyaml` is optional — without it the folder layout below is used and
`config.yaml` is ignored.

## Where things go

| Folder | What lands there |
| --- | --- |
| `inbox/` | you drop exports here |
| `archive/` | files that imported, moved out of the way so the folder does not accumulate |
| `rejected/` | files nothing could read, each with a `.txt` beside it saying why |
| `out/` | `dashboard-YYYY-MM-DD.html` — the thing you open |

Change any of these in `config.yaml`, including to a UNC path if the inbox lives
on a share. Nothing in the script needs editing.

## Read this before you send the output anywhere

**The generated page contains employee records.** The dashboard itself holds no
data and is safe to pass around; a file out of `out/` is a personnel export with
names, locations and assessment scores in it. Treat it as you would the source
spreadsheets. `.gitignore` keeps all four folders out of the repository.

The page still makes no network calls — the data stays in whatever file you put
it in, and goes nowhere on its own. But that file is now the sensitive thing.

## How it decides what a file is

By the columns a sheet carries, never by its filename. Rename an export to
`export (3).xlsx` and it still lands in the right place; give it the right name
with the wrong columns and it is rejected.

The signatures live in `../config/sources.json`. `../build.sh` inlines that same
file into the dashboard, so the page and this script cannot disagree about what
makes a sheet the LMS export. To recognise a differently-shaped export, widen
its `must` list there and re-run `build.sh` — not by adding a filename rule.

## What it deliberately does not do

**It computes no KPIs.** It reads spreadsheets and emits rows; the page does
every calculation, filter and rate. Two implementations of the same number in
two languages drift, and the first you hear of it is a figure in a meeting that
nobody can reconcile.

Concretely: the rows are embedded in the shape the page's own reader already
produces, and are fed through the same detection a dragged file goes through.
There is one ingestion path, not two. `../test/imported.mjs` asserts that the
generated page's ten tiles match the dragged path exactly.

## Things it will tell you about

- **A file nothing recognised** — moved to `rejected/`, with the columns it
  actually found written beside it, so you can see whether a header was renamed.
- **Two files claiming the same report** — their rows are added together, not
  replaced, so a stale copy left in the folder inflates that source's totals.
  It says so rather than quietly doubling a number.
- **A formula with no cached value** — a workbook written by a tool that never
  calculated arrives with empty cells. Those come through as gaps, not zeros.

## Re-running

Safe. The output for a given day is overwritten, and imported files have already
moved to `archive/`, so a second run with an empty inbox simply tells you there
is nothing to do.
