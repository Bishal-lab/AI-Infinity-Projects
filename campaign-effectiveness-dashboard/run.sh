#!/usr/bin/env bash
# One-command start. Creates a virtual environment on first run.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if [[ ! -d .venv ]]; then
  echo "Creating virtual environment…"
  "$PYTHON" -m venv .venv
  if [[ -d wheelhouse ]]; then
    .venv/bin/pip install --no-index --find-links=wheelhouse -r requirements.txt
  else
    .venv/bin/pip install -r requirements.txt
  fi
fi

exec .venv/bin/streamlit run app/main.py "$@"
