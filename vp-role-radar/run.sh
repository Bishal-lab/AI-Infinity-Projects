#!/usr/bin/env bash
# Scan for openings and send the digest. Suitable for a crontab line:
#   37 2 * * 1-5  /path/to/vp-role-radar/run.sh >> /var/log/vp-role-radar.log 2>&1
# (02:37 UTC = 08:07 IST; if the host runs on IST, use 7 8 * * 1-5 instead.)
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi

exec "$PYTHON" -m radar "${@:-run}"
