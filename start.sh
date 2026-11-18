#!/bin/sh
# One command: sets up the local environment on first run, then starts the review desk.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q requests beautifulsoup4 flask || exit 1
fi
exec .venv/bin/python -W ignore app.py
