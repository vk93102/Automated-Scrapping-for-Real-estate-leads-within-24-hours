#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
LOCK_DIR="$ROOT/tmp/lapaz_interval.lock"
LOCK_PID_FILE="$LOCK_DIR/pid"

mkdir -p "$ROOT/tmp"
acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" > "$LOCK_PID_FILE"
    return 0
  fi

  if [ -f "$LOCK_PID_FILE" ]; then
    local pid
    pid="$(cat "$LOCK_PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$LOCK_DIR"
      if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "$$" > "$LOCK_PID_FILE"
        echo "[lapaz-cron] cleared stale lock from pid=$pid"
        return 0
      fi
    fi
  else
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "$$" > "$LOCK_PID_FILE"
      echo "[lapaz-cron] cleared legacy stale lock"
      return 0
    fi
  fi

  return 1
}

if ! acquire_lock; then
  echo "[lapaz-cron] another run is active; skipping overlap"
  exit 0
fi
cleanup_lock() {
  rm -f "$LOCK_PID_FILE" 2>/dev/null || true
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

LOOKBACK_DAYS="${LAPAZ_LOOKBACK_DAYS:-2}"
WORKERS="${LAPAZ_WORKERS:-1}"
# CRITICAL: ocr_limit=0 means process ALL documents with OCR + Groq LLM
# This is REQUIRED for proper data extraction (trustor, trustee, address, etc)
OCR_LIMIT="${LAPAZ_OCR_LIMIT:-0}"
VERBOSE_FLAG="${LAPAZ_VERBOSE:-0}"

ARGS=(
  --lookback-days "$LOOKBACK_DAYS"
  --workers "$WORKERS"
  --ocr-limit "$OCR_LIMIT"
)

if [ "$VERBOSE_FLAG" = "1" ]; then
  ARGS+=(--verbose)
fi

exec "$PY_BIN" "$DIR/run_lapaz_interval.py" \
  "${ARGS[@]}"
