#!/usr/bin/env bash
# run_cron.sh — Cron wrapper for the Maricopa NS scraper.
#
# Runs every 10 minutes. Always processes YESTERDAY's records.
# Skips already-stored recording numbers (--only-new).
#
# Crontab entry:
#   */10 * * * * /Users/vishaljha/Desktop/web\ scrapping/automation/run_cron.sh >> /Users/vishaljha/Desktop/web\ scrapping/automation/logs/cron_master.log 2>&1
#
set -euo pipefail

# ── Absolute path to project root ──────────────────────────────────────────
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# ── PATH: cron has a minimal environment ───────────────────────────────────
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ── Compute yesterday (macOS BSD date) ────────────────────────────────────
YESTERDAY="$(date -v-1d +%Y-%m-%d)"

# ── Load env vars (DATABASE_URL, GROQ_API_KEY, etc.) ──────────────────────
if [[ -f "$DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

# Fall back to LLAMA_API_KEY if GROQ_API_KEY is missing
if [[ -z "${GROQ_API_KEY:-}" ]] && [[ -n "${LLAMA_API_KEY:-}" ]]; then
  export GROQ_API_KEY="$LLAMA_API_KEY"
fi

# ── Validate required vars ─────────────────────────────────────────────────
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
mkdir -p "$LOG_DIR" "$DIR/output"

LOG_FILE="$LOG_DIR/cron_${YESTERDAY}.log"

# Rotate logs older than 30 days
find "$LOG_DIR" -name "cron_*.log" -mtime +30 -delete 2>/dev/null || true

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
{
  echo ""
  echo "======================================================"
  echo "[$TIMESTAMP] run_cron.sh starting  (yesterday=$YESTERDAY)"
  echo "======================================================"
} >> "$LOG_FILE"

# ── Run scraper ────────────────────────────────────────────────────────────
#   --document-code NS    → Maricopa API code for N/TR SALE records
#   --begin-date / --end-date → always yesterday
#   --limit 0             → no cap, get every record found
#   --only-new            → skip recording numbers already in DB
#   --pdf-mode memory     → OCR from in-memory bytes (no temp files)
#   --sleep 0.3           → polite delay between requests
EXIT_CODE=0
"$PY_BIN" -m maricopa_scraper.scraper \
  --document-code "NS" \
  --begin-date "$YESTERDAY" \
  --end-date   "$YESTERDAY" \
  --limit      0 \
  --pdf-mode   memory \
  --sleep      0.3 \
  --only-new \
  --workers    5 \
  --db-url     "$DATABASE_URL" \
  --log-level  INFO \
  --out-json   "$DIR/output/cron_${YESTERDAY}.json" \
  --out-csv    "$DIR/output/cron_${YESTERDAY}.csv" \
  >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?

FINISH_TS="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[$FINISH_TS] run_cron.sh finished (exit=$EXIT_CODE)" >> "$LOG_FILE"
exit $EXIT_CODE
