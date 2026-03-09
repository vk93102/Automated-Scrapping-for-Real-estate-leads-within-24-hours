#!/bin/bash
# Minimal wrapper — all real logic is in run_cron.py (avoids macOS cron EDEADLK).
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/python" "$DIR/run_cron.py"
