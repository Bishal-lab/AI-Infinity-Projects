#!/usr/bin/env bash
# Build and send today's brief. Suitable for a crontab line:
#   30 2 * * *  /path/to/bfsi-insurance-digest-bot/run.sh >> /var/log/bfsi-digest.log 2>&1
# (02:30 UTC = 08:00 IST; if the host runs on IST, use 0 8 * * * instead.)
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi

exec "$PYTHON" -m bot "${@:-run}"
