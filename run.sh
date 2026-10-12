#!/usr/bin/env bash
# One command: sets itself up the first time, then starts Stat Asker and opens it in your browser.
#   ./run.sh                      quick start (play-by-play for the last 10 seasons)
#   ./run.sh --history full       also index 1950 onward (about an hour, ~4 GB, runs in the background)
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "Python 3.11+ is required (https://www.python.org/downloads/)."; exit 1; }

if [ ! -d .venv ]; then
  echo "First run: creating a private Python environment…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
if ! python -c "import fastapi, uvicorn, httpx" 2>/dev/null; then
  echo "Installing dependencies (a few seconds, nothing is compiled)…"
  pip install --quiet --disable-pip-version-check --only-binary=:all: -r requirements.txt
fi

exec python -m mlbasker serve --open "$@"
