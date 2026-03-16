#!/usr/bin/env bash
# =============================================================================
# run_pipeline.sh — Production cron wrapper for Maricopa County Recorder scraper
#
# Schedule: Every 10 minutes (*/10 * * * *)
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

# Python selection order:
#  1) explicit PY_BIN env override
#  2) repo-local virtualenv
#  3) pyenv-selected python
#  4) system python3
PY_BIN_CANDIDATES=()
if [[ -n "${PY_BIN:-}" ]]; then
  PY_BIN_CANDIDATES+=("$PY_BIN")
fi
PY_BIN_CANDIDATES+=("$DIR/.venv/bin/python")
if command -v pyenv >/dev/null 2>&1; then
  _PYENV_PY="$(pyenv which python 2>/dev/null || true)"
  [[ -n "${_PYENV_PY:-}" ]] && PY_BIN_CANDIDATES+=("$_PYENV_PY")
fi
_SYS_PY="$(command -v python3 2>/dev/null || true)"
[[ -n "${_SYS_PY:-}" ]] && PY_BIN_CANDIDATES+=("$_SYS_PY")

PY_BIN=""
for candidate in "${PY_BIN_CANDIDATES[@]}"; do
  if [[ -x "$candidate" ]] && "$candidate" -c "import psycopg" >/dev/null 2>&1; then
    PY_BIN="$candidate"
    break
  fi
done

# Fallback to first executable candidate even if psycopg is missing,
# so we can show a clear install hint below.
if [[ -z "$PY_BIN" ]]; then
  for candidate in "${PY_BIN_CANDIDATES[@]}"; do
    if [[ -x "$candidate" ]]; then
      PY_BIN="$candidate"
      break
    fi
  done
fi

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

# ── Arizona time by default ──────────────────────────────────────────────────
# Maricopa County runs on Phoenix time year-round (no DST switch).
export TZ="${TZ:-America/Phoenix}"

# ── Validate required env vars ────────────────────────────────────────────────
if [[ ! -x "$PY_BIN" ]]; then
  echo "[pipeline] FATAL: Python executable not found (resolved path: $PY_BIN)" | tee -a "$LOG_FILE"
  exit 1
fi

if ! "$PY_BIN" -c "import psycopg" >/dev/null 2>&1; then
  echo "[pipeline] FATAL: Missing dependency 'psycopg' for $PY_BIN" | tee -a "$LOG_FILE"
  echo "[pipeline] Run: $PY_BIN -m pip install -r $DIR/requirements.txt" | tee -a "$LOG_FILE"
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

# ── Optional US proxy rotation ────────────────────────────────────────────────
USE_PROXY_FLAG=()
if [[ "${USE_PROXY:-false}" == "true" ]]; then
  USE_PROXY_FLAG=(--use-proxy)
  if [[ -z "${PROXY_LIST_PATH:-}" ]]; then
    echo "[pipeline] WARNING: USE_PROXY=true but PROXY_LIST_PATH is not set" | tee -a "$LOG_FILE"
  fi
fi

# ── Log run header ────────────────────────────────────────────────────────────
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
{
  echo ""
  echo "============================================================"
  echo "[$TIMESTAMP] run_pipeline.sh starting (date=$TODAY, tz=$TZ, use_proxy=${USE_PROXY:-false})"
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
  --document-code "N/TR SALE" \
  --days 1 \
  --limit 0 \
  --pdf-mode memory \
  --sleep 0.3 \
  --only-new \
  ${USE_PROXY_FLAG[@]+"${USE_PROXY_FLAG[@]}"}  \
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