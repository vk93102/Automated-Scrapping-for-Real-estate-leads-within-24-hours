#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

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

PYTHON_BIN="$(pick_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "No usable python found with required packages (requests, bs4, Pillow, playwright, psycopg)." >&2
  exit 1
fi

LOOKBACK_DAYS="${COCONINO_LOOKBACK_DAYS:-2}"
OCR_LIMIT="${COCONINO_OCR_LIMIT:-0}"

exec "$PYTHON_BIN" "$SCRIPT_DIR/run_conino_interval.py" \
  --once \
  --lookback-days "$LOOKBACK_DAYS" \
  --ocr-limit "$OCR_LIMIT"
