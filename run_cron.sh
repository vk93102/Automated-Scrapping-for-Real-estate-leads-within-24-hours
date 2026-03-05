#!/usr/bin/env bash
# run_cron.sh — Cron wrapper for the Maricopa scraper.
#
# Called every 10 minutes by crontab.
# Fetches up to 15 NEW documents for TODAY and stores them in Supabase.
#
# Crontab entry (added automatically — do not edit manually here):
#   */10 * * * * /Users/vishaljha/Desktop/web\ scrapping/automation/run_cron.sh
#
set -euo pipefail

# ── Absolute path to project root ──────────────────────────────────────────
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# ── PATH: cron has a minimal environment ───────────────────────────────────
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ── Load env vars (DATABASE_URL, API_TOKEN, etc.) ──────────────────────────
if [[ -f "$DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

# ── Python binary ──────────────────────────────────────────────────────────
PY_BIN="$DIR/.venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  echo "[run_cron.sh] ERROR: venv python not found at $PY_BIN" >&2
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[run_cron.sh] ERROR: DATABASE_URL is not set" >&2
  exit 1
fi

# ── Log setup ─────────────────────────────────────────────────────────────
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cron_$(date +%Y-%m-%d).log"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
echo "" >> "$LOG_FILE"
echo "======================================================" >> "$LOG_FILE"
echo "[$TIMESTAMP] run_cron.sh starting" >> "$LOG_FILE"
echo "======================================================" >> "$LOG_FILE"

# ── Run scraper: TODAY, 15 documents, only new, in-memory OCR ─────────────
"$PY_BIN" -m automation.maricopa_scraper.scraper \
  --document-code ALL \
  --days 1 \
  --limit 50 \
  --pdf-mode memory \
  --sleep 0.5 \
  --only-new \
  --db-url "$DATABASE_URL" \
  --log-level INFO \
  --out-json "$DIR/output/cron_latest.json" \
  --out-csv  "$DIR/output/cron_latest.csv" \
  >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] run_cron.sh finished (exit=$EXIT_CODE)" >> "$LOG_FILE"
exit $EXIT_CODE
