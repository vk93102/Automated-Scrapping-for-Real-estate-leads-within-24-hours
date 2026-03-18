#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
LOCK_DIR="$ROOT/tmp/navajo_interval.lock"

mkdir -p "$ROOT/tmp"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[navajo-cron] another run is active; skipping overlap"
  exit 0
fi
cleanup_lock() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT

pick_python() {
  local c
  for c in \
    "$ROOT/.venv/bin/python" \
    "/Users/vishaljha/.pyenv/versions/3.10.13/bin/python" \
    "$(pyenv which python 2>/dev/null || true)" \
    "$(command -v python3 || true)" \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3"; do
    if [ -n "${c:-}" ] && [ -x "$c" ]; then
      if "$c" - <<'PY' >/dev/null 2>&1
import requests, bs4, PIL, playwright, psycopg
PY
      then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}
PY_BIN="$(pick_python || true)"
if [ -z "$PY_BIN" ]; then
  echo "No usable python found with required packages (requests, bs4, Pillow, playwright, psycopg)." >&2
  exit 1
fi

LOOKBACK_DAYS="${NAVAJO_LOOKBACK_DAYS:-2}"
WORKERS="${NAVAJO_WORKERS:-3}"
# CRITICAL: ocr_limit=0 means process ALL documents with OCR + Groq LLM
# This is REQUIRED for proper data extraction (trustor, trustee, address, etc)
OCR_LIMIT="${NAVAJO_OCR_LIMIT:-0}"

exec "$PY_BIN" "$DIR/run_navajo_interval.py" \
  --once \
  --lookback-days "$LOOKBACK_DAYS" \
  --workers "$WORKERS" \
  --ocr-limit "$OCR_LIMIT"
