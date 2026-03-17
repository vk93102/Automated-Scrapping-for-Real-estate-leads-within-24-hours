#!/usr/bin/env bash
set -uo pipefail

# Continuous Maricopa pipeline runner (no cron required)
# - Runs forever at a fixed interval
# - Uses only recent window by default (days=1) to avoid large-range API 500s
# - Retries automatically on failures in next loop

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

mkdir -p logs output
LOG_FILE="${LOG_FILE:-$ROOT_DIR/logs/maricopa_daemon.log}"

# Load .env if present
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# Resolve python
if [[ -n "${PY_BIN:-}" && -x "${PY_BIN}" ]]; then
  PYTHON_BIN="$PY_BIN"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  PYTHON_BIN="python"
fi

# Defaults (override with env vars in Coolify)
INTERVAL_SECONDS="${INTERVAL_SECONDS:-600}"   # 10 min for near real-time
DAYS_WINDOW="${DAYS_WINDOW:-1}"               # keep small to avoid search API 500
DOC_CODE="${DOC_CODE:-ALL}"
WORKERS="${WORKERS:-4}"
SLEEP_BETWEEN_DOCS="${SLEEP_BETWEEN_DOCS:-0.3}"
PDF_MODE="${PDF_MODE:-memory}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
TZ="${TZ:-America/Phoenix}"
export TZ

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[$(date '+%F %T')] FATAL: DATABASE_URL is missing" | tee -a "$LOG_FILE"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[$(date '+%F %T')] FATAL: Python not executable: $PYTHON_BIN" | tee -a "$LOG_FILE"
  exit 1
fi

echo "[$(date '+%F %T')] maricopa daemon started | interval=${INTERVAL_SECONDS}s days=${DAYS_WINDOW} doc_code=${DOC_CODE}" | tee -a "$LOG_FILE"

while true; do
  START_TS="$(date +%s)"
  echo "[$(date '+%F %T')] run start" | tee -a "$LOG_FILE"

  "$PYTHON_BIN" -m maricopa.scraper \
    --document-code "$DOC_CODE" \
    --days "$DAYS_WINDOW" \
    --limit 0 \
    --pdf-mode "$PDF_MODE" \
    --sleep "$SLEEP_BETWEEN_DOCS" \
    --workers "$WORKERS" \
    --only-new \
    --db-url "$DATABASE_URL" \
    --log-level "$LOG_LEVEL" \
    --out-json output/pipeline_latest.json \
    --out-csv output/pipeline_latest.csv \
    --out-csv-dated \
    >> "$LOG_FILE" 2>&1

  EXIT_CODE=$?
  if [[ $EXIT_CODE -eq 0 ]]; then
    echo "[$(date '+%F %T')] run success" | tee -a "$LOG_FILE"
  else
    echo "[$(date '+%F %T')] run failed exit=$EXIT_CODE (will retry next loop)" | tee -a "$LOG_FILE"
  fi

  if [[ "${RUN_ONCE:-0}" == "1" ]]; then
    echo "[$(date '+%F %T')] RUN_ONCE=1 set, exiting" | tee -a "$LOG_FILE"
    exit $EXIT_CODE
  fi

  NOW_TS="$(date +%s)"
  ELAPSED=$((NOW_TS - START_TS))
  SLEEP_FOR=$((INTERVAL_SECONDS - ELAPSED))
  if [[ $SLEEP_FOR -lt 30 ]]; then
    SLEEP_FOR=30
  fi
  echo "[$(date '+%F %T')] sleeping ${SLEEP_FOR}s" | tee -a "$LOG_FILE"
  sleep "$SLEEP_FOR"
done
