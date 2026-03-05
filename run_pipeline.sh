#!/usr/bin/env bash
# =============================================================================
# run_pipeline.sh — Production cron wrapper for Maricopa County Recorder scraper
#
# Schedule: Every 2 hours, 12 times per day (0 */2 * * *)
#
# Pipeline per run:
#   1. Fetch today's recording numbers from Maricopa public API
#   2. Check DB → skip already-processed records
#   3. For new records: fetch metadata → check PDF accessibility
#   4. If PDF accessible: OCR in-memory → LLaMA (Groq llama-3.1-8b-instant)
#   5. Store document + extracted properties in Supabase Postgres
#   6. Track run stats in pipeline_runs table
# =============================================================================
set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PY_BIN="$DIR/.venv/bin/python"
LOG_DIR="$DIR/logs"
OUTPUT_DIR="$DIR/output"
TODAY="$(date +%Y-%m-%d)"

# ── Ensure output dirs exist ──────────────────────────────────────────────────
mkdir -p "$LOG_DIR" "$OUTPUT_DIR"

# ── Log file: one per day, append across all runs ─────────────────────────────
LOG_FILE="$LOG_DIR/pipeline_${TODAY}.log"

# ── Log rotation: delete logs older than 30 days ──────────────────────────────
find "$LOG_DIR" -name "pipeline_*.log" -mtime +30 -delete 2>/dev/null || true
find "$LOG_DIR" -name "cron_*.log"     -mtime +30 -delete 2>/dev/null || true

# ── Minimal PATH for cron environment ────────────────────────────────────────
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ -f "$DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

# ── Validate required env vars ────────────────────────────────────────────────
if [[ ! -x "$PY_BIN" ]]; then
  echo "[pipeline] FATAL: Python venv not found at $PY_BIN" | tee -a "$LOG_FILE"
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[pipeline] FATAL: DATABASE_URL is not set" | tee -a "$LOG_FILE"
  exit 1
fi

# ── Export GROQ API key (env can use either name) ─────────────────────────────
# Prefer GROQ_API_KEY; fall back to LLAMA_API_KEY from .env
if [[ -z "${GROQ_API_KEY:-}" ]] && [[ -n "${LLAMA_API_KEY:-}" ]]; then
  export GROQ_API_KEY="$LLAMA_API_KEY"
fi

if [[ -z "${GROQ_API_KEY:-}" ]]; then
  echo "[pipeline] WARNING: GROQ_API_KEY not set — LLM extraction will use fallback" | tee -a "$LOG_FILE"
fi

# ── Log run header ────────────────────────────────────────────────────────────
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
{
  echo ""
  echo "============================================================"
  echo "[$TIMESTAMP] run_pipeline.sh starting (date=$TODAY)"
  echo "============================================================"
} >> "$LOG_FILE"

# ── Run the scraper ───────────────────────────────────────────────────────────
#   --document-code ALL    : capture all doc types (DEED TRST, WAR DEED, etc.)
#   --days 1               : today only (begin_date = today-1 day to today)
#   --limit 500            : process up to 500 new docs per 2-hour window
#   --pdf-mode memory      : OCR from in-memory bytes (no disk writes)
#   --sleep 0.3            : polite delay between requests
#   --only-new             : skip already-processed recording numbers
#   --out-csv-dated        : write daily CSV for audit trail
#   --log-level INFO       : INFO-level logging to log file

EXIT_CODE=0
"$PY_BIN" -m maricopa_scraper.scraper \
  --document-code ALL \
  --days 1 \
  --limit 500 \
  --pdf-mode memory \
  --sleep 0.3 \
  --only-new \
  --db-url "$DATABASE_URL" \
  --log-level INFO \
  --out-json  "$OUTPUT_DIR/pipeline_latest.json" \
  --out-csv   "$OUTPUT_DIR/pipeline_latest.csv" \
  --out-csv-dated \
  >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?

# ── Log run footer ────────────────────────────────────────────────────────────
FINISH_TS="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[$FINISH_TS] run_pipeline.sh finished (exit=$EXIT_CODE)" >> "$LOG_FILE"

exit $EXIT_CODE
