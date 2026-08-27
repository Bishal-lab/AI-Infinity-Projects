#!/bin/sh
# Concatenate src/ into the single self-contained dashboard.html.
# The parts are separate files only so they stay editable; the artifact is one
# page with no build tooling, no imports and nothing to fetch at runtime.
set -e
cd "$(dirname "$0")"
{
  cat src/00_page.html
  for part in 01_xlsx 02_sources 03_kpis 04_charts 05_view 06_wiring; do
    sed 's/^export //' "src/$part.js"
    echo
  done
  echo '</script>'
} > dashboard.html
echo "dashboard.html — $(wc -c < dashboard.html) bytes"
