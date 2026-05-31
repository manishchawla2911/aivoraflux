#!/usr/bin/env bash
# Start nexaflow-ai (no API keys required — uses offline stubs)
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Virtualenv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt python-multipart"
  exit 1
fi

PORT="${PORT:-8000}"
if lsof -i ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "Already running on http://127.0.0.1:$PORT"
  exit 0
fi

.venv/bin/python -u main.py
