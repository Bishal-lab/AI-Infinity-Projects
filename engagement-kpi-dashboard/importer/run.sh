#!/bin/sh
# Import whatever is in inbox/ and write a populated dashboard to out/.
cd "$(dirname "$0")"
python3 import_exports.py "$@"
