#!/usr/bin/env bash
# Start the Q50 TestBench GUI (creates the venv on first run)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m q50tb "$@"
