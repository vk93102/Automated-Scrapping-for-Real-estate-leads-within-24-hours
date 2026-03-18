#!/usr/bin/env bash
# Greenlee live pipeline cron runner - periodic execution with database storage

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
LOCK_DIR="$ROOT/tmp/greenlee_live_cron.lock"

mkdir -p "$ROOT/tmp"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[greenlee-live-cron] another run is active; skipping overlap"
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

# Configuration
LOOKBACK_DAYS="${GREENLEE_LIVE_LOOKBACK_DAYS:-7}"
WORKERS="${GREENLEE_LIVE_WORKERS:-3}"
OCR_LIMIT="${GREENLEE_LIVE_OCR_LIMIT:-0}"
VERBOSE_FLAG="${GREENLEE_LIVE_VERBOSE:-0}"

ARGS=(
  --lookback-days "$LOOKBACK_DAYS"
  --workers "$WORKERS"
  --ocr-limit "$OCR_LIMIT"
)

if [ "$VERBOSE_FLAG" = "1" ]; then
  ARGS+=(--verbose)
fi

# Load environment
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT/.env"
  set +a
fi

exec "$PY_BIN" "$DIR/run_greenlee_live_cron.py" "${ARGS[@]}"
