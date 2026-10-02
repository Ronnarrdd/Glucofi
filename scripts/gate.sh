#!/bin/sh
# Tests rapides (gate) : déterministes, locaux, lancés à chaque commit.
set -eu
cd "$(dirname "$0")/.."
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/glucofi-mpl}"
export MPLBACKEND=Agg
PYTHON="${PYTHON:-python3}"
make -s -C services/device/accuchek-src test
exec "$PYTHON" -m unittest discover -t . -s . -p 'test_*.py' "$@"
