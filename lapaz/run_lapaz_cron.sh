#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

pick_python() {
  local c
  for c in "$ROOT/.venv/bin/python" "$(command -v python3 || true)" "/opt/homebrew/bin/python3" "/usr/local/bin/python3"; do
    if [ -n "${c:-}" ] && [ -x "$c" ]; then
      if "$c" - <<'PY' >/dev/null 2>&1
import requests, bs4, PIL
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
  echo "No usable python found with required packages (requests, bs4, Pillow)." >&2
  exit 1
fi

exec "$PY_BIN" "$DIR/run_lapaz_cron.py"
