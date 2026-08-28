#!/usr/bin/env python3
"""Build a populated dashboard from the exports sitting in the inbox folder.

Reads every .xlsx in `inbox/`, embeds the rows in a copy of dashboard.html, and
writes it to `out/`. Open that file and the dashboard is already filled in —
nobody drags anything.

The one rule this script obeys: **it does not compute a single KPI.** It reads
spreadsheets and emits rows. Detection, filtering and all ten KPIs stay in the
page, where they are already tested. Two implementations of the same number in
two languages drift, and the first you hear of it is a figure in a meeting that
nobody can reconcile.

That is why the payload is emitted in the exact shape the page's own
`toRecords()` produces, and is fed through the very same `claim()` a dropped
file goes through. This script never needs to know which file is which.

No network access, at any point.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover - the message is the whole point
    sys.exit("openpyxl is not installed. Run:  pip install openpyxl pyyaml")

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "sources": "../config/sources.json",
    "inbox": "inbox",
    "archive": "archive",
    "rejected": "rejected",
    "out": "out",
    "dashboard": "../dashboard.html",
}


def load_config() -> dict:
    """Paths from config.yaml, falling back to the defaults beside this file.

    PyYAML is optional on purpose: the defaults are the common case, and a
    missing library should not stop someone who never edited the config.
    """
    cfg = dict(DEFAULTS)
    path = HERE / "config.yaml"
    if path.exists():
        try:
            import yaml
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            cfg.update({k: v for k, v in loaded.items() if k in DEFAULTS and v})
        except ImportError:
            print("! pyyaml not installed; using the default folder layout")
    return {k: (HERE / v).resolve() for k, v in cfg.items()}


def load_sources(path: Path) -> list[dict]:
    """The five report signatures — the same file build.sh inlines into the page.

    Reading it here rather than hard-coding the columns is the whole reason this
    script can say a file is unusable. Both sides agree by construction about
    what makes a sheet the LMS export.
    """
    return json.loads(path.read_text(encoding="utf-8"))["sources"]


def claim(headers: list[str], sources: list[dict]) -> dict | None:
    """Which report a sheet is, by the columns it carries — never by filename.

    Longest signature first, exactly as the page does it, so a sheet satisfying
    two signatures goes to the more specific one.
    """
    have = {h for h in headers if h}
    for src in sorted(sources, key=lambda s: -len(s["must"])):
        if all(col in have for col in src["must"]):
            return src
    return None


def cell_value(value):
    """One cell, as JSON can carry it.

    Dates become ISO text because JSON has no date type. The page parses that
    with the same `asDate()` it already uses on the exports, which carry their
    dates as text too — so this is the format it is best tested against.
    """
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if (
            value.hour == value.minute == value.second == 0
        ) else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, float) and value != value:      # NaN
        return None
    return value


def read_sheets(path: Path) -> list[dict]:
    """Every sheet of a workbook as {headers, records}.

    `data_only=True` so a formula cell yields its cached value rather than the
    formula text. A file written by a tool that never calculated has no cached
    values, and those cells arrive as None — which is honest, and renders as a
    gap rather than as a zero.
    """
    book = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = []
    try:
        for sheet in book.worksheets:
            rows = sheet.iter_rows(values_only=True)
            try:
                header_row = next(rows)
            except StopIteration:
                continue
            headers = [
                "" if h is None else str(h).strip() for h in header_row
            ]
            if not any(headers):
                continue

            records = []
            for row in rows:
                if row is None or all(c is None or c == "" for c in row):
                    continue
                record = {}
                for i, name in enumerate(headers):
                    if name:
                        record[name] = cell_value(row[i]) if i < len(row) else None
                records.append(record)

            if records:
                sheets.append({"sheet": sheet.title, "headers": headers,
                               "records": records})
    finally:
        book.close()
    return sheets


def populate(template: str, payload: list[dict], stamp: str) -> str:
    """Insert the payload ahead of the page's own script block.

    `json.dumps` with `</` escaped: a string in the data containing `</script>`
    would otherwise close the tag early and break the page. Nothing in these
    exports is likely to, but the failure would be silent and total.
    """
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")
    block = (
        "<script>\n"
        f"window.__PRELOADED__ = {blob};\n"
        f"window.__IMPORTED_AT__ = {json.dumps(stamp)};\n"
        "</script>\n"
    )
    marker = "<script>"
    at = template.find(marker)
    if at < 0:
        raise SystemExit("dashboard.html has no <script> block to insert before")
    return template[:at] + block + template[at:]


def main() -> int:
    cfg = load_config()
    for key in ("inbox", "archive", "rejected", "out"):
        cfg[key].mkdir(parents=True, exist_ok=True)

    if not cfg["dashboard"].exists():
        print(f"! no dashboard template at {cfg['dashboard']}")
        print("  run ../build.sh first")
        return 1

    files = sorted(p for p in cfg["inbox"].glob("*.xlsx") if not p.name.startswith("~$"))
    if not files:
        print(f"Nothing to import — {cfg['inbox']} is empty.")
        print("Drop the exports in there and run this again.")
        return 1

    sources = load_sources(cfg["sources"])

    payload, imported, rejected = [], [], []
    for path in files:
        try:
            sheets = read_sheets(path)
        except Exception as err:
            rejected.append((path, f"could not be read: {err}"))
            continue
        if not sheets:
            rejected.append((path, "no sheet had a header row and data under it"))
            continue

        claimed, rows, seen = [], 0, []
        for sheet in sheets:
            src = claim(sheet["headers"], sources)
            if not src:
                seen.append(f'{sheet["sheet"]}: {", ".join(h for h in sheet["headers"] if h) or "(no headers)"}')
                continue
            claimed.append(src["label"])
            rows += len(sheet["records"])
            payload.append({
                "file": path.name,
                "sheet": sheet["sheet"],
                "headers": sheet["headers"],
                "records": sheet["records"],
            })

        if claimed:
            imported.append((path, rows, ", ".join(claimed)))
        else:
            # Nothing in the file matched a known report. Embedding it anyway
            # would bloat the page and, worse, move the file to archive/ as
            # though it had been used.
            rejected.append((path, "no sheet matched a known report. Columns found — "
                             + " | ".join(seen)))

    if not payload:
        print("Nothing could be read. See rejected/ for why.")
        for path, why in rejected:
            _reject(path, why, cfg["rejected"])
        return 1

    # Two files claiming the same report are concatenated, not replaced — so a
    # stale copy left in the folder silently doubles that source's numbers. A
    # drag-and-drop user picks five files and sees them; a folder accumulates.
    claims: dict[str, list[str]] = {}
    for sheet in payload:
        for src in sources:
            if all(col in set(sheet["headers"]) for col in src["must"]):
                claims.setdefault(src["label"], []).append(sheet["file"])
                break
    doubled = {label: files for label, files in claims.items() if len(set(files)) > 1}
    if doubled:
        print()
        for label, names in doubled.items():
            print(f"! {label} came from {len(set(names))} files: {', '.join(sorted(set(names)))}")
        print("  Their rows are added together, not replaced — that source's")
        print("  totals will be inflated. Remove the stale copies and re-run.")

    stamp = dt.datetime.now().strftime("%d %b %Y at %H:%M")
    page = populate(cfg["dashboard"].read_text(encoding="utf-8"), payload, stamp)

    out = cfg["out"] / f"dashboard-{dt.date.today().isoformat()}.html"
    out.write_text(page, encoding="utf-8")

    for path, count, what in imported:
        print(f"  {path.name} — {what}, {count:,} rows")
        _move(path, cfg["archive"])
    for path, why in rejected:
        print(f"  {path.name} — REJECTED, {why}")
        _reject(path, why, cfg["rejected"])

    print()
    print(f"Wrote {out}")
    print(f"  {len(imported)} file(s) imported, {len(rejected)} rejected")
    print()
    print("Open that file in a browser. It contains employee records — keep it")
    print("where you would keep any other personnel export.")
    return 0


def _unique(target: Path) -> Path:
    """A destination that does not overwrite yesterday's file of the same name."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return target.with_name(f"{stem}.{stamp}{suffix}")


def _move(path: Path, into: Path) -> None:
    shutil.move(str(path), str(_unique(into / path.name)))


def _reject(path: Path, why: str, into: Path) -> None:
    dest = _unique(into / path.name)
    shutil.move(str(path), str(dest))
    # The reason sits beside the file, so nobody has to find this run's log.
    dest.with_suffix(dest.suffix + ".txt").write_text(
        f"{dt.datetime.now().isoformat(timespec='seconds')}\n{path.name}\n{why}\n",
        encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
