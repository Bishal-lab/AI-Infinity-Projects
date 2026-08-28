#!/bin/sh
# Concatenate src/ into the single self-contained dashboard.html.
# The parts are separate files only so they stay editable; the artifact is one
# page with no build tooling, no imports and nothing to fetch at runtime.
set -e
cd "$(dirname "$0")"
{
  cat src/00_page.html
  for part in 01_xlsx 02_sources 03_kpis 04_charts 05_view 06_wiring; do
    # 02_sources carries a marker where config/sources.json belongs; the
    # importer reads that same file, so neither can drift from the other.
    sed 's/^export //' "src/$part.js" | python3 -c '
import json, sys
page = sys.stdin.read()
if "/*__SOURCES__*/" in page:
    rows = json.load(open("config/sources.json"))["sources"]
    page = page.replace("/*__SOURCES__*/[]", json.dumps(rows, ensure_ascii=False))
sys.stdout.write(page)'
    echo
  done
  echo '</script>'
} > dashboard.html
echo "dashboard.html — $(wc -c < dashboard.html) bytes"
