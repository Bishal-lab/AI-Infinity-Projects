#!/usr/bin/env bash
# Build an offline install bundle for an air-gapped server.
#
# Run this on a machine that HAS internet access and matches the target's
# Python version and platform, then copy the whole project (including
# wheelhouse/) across.
#
#   ./scripts/build_wheelhouse.sh
#   # on the target host:
#   pip install --no-index --find-links=wheelhouse -r requirements.txt
#
# Override the target platform if the build machine differs from the server:
#   PLATFORM=manylinux2014_x86_64 PYVER=3.11 ./scripts/build_wheelhouse.sh

set -euo pipefail
cd "$(dirname "$0")/.."

PLATFORM="${PLATFORM:-}"
PYVER="${PYVER:-}"
DEST="${DEST:-wheelhouse}"

mkdir -p "$DEST"
ARGS=(download -r requirements.txt -d "$DEST")

if [[ -n "$PLATFORM" ]]; then
  # Cross-building for another platform requires binary-only wheels; pip
  # cannot build a source distribution for a platform it is not running on.
  ARGS+=(--only-binary=:all: --platform "$PLATFORM")
  [[ -n "$PYVER" ]] && ARGS+=(--python-version "$PYVER")
fi

python -m pip "${ARGS[@]}"

echo
echo "Wheels written to $DEST/ ($(ls -1 "$DEST" | wc -l) files, $(du -sh "$DEST" | cut -f1))"
echo "Copy the project across, then on the target host run:"
echo "  pip install --no-index --find-links=$DEST -r requirements.txt"
