#!/usr/bin/env bash
set -uo pipefail

# One-shot Maricopa pipeline runner (legacy filename kept for compatibility)
# - Runs exactly once
# - Exits immediately after success/failure

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# Accept and ignore legacy --once flag so existing commands still work.
if [[ "${1:-}" == "--once" ]]; then
  shift
fi

mkdir -p logs output
LOG_FILE="${LOG_FILE:-$ROOT_DIR/logs/maricopa_once.log}"

# Load .env if present
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# Resolve python (prefer an interpreter that already has required deps)
PYTHON_BIN=""
CANDIDATES=()
if [[ -n "${PY_BIN:-}" ]]; then CANDIDATES+=("$PY_BIN"); fi
CANDIDATES+=("$ROOT_DIR/.venv/bin/python")
if command -v pyenv >/dev/null 2>&1; then
  _PYENV_PY="$(pyenv which python 2>/dev/null || true)"
  [[ -n "${_PYENV_PY:-}" ]] && CANDIDATES+=("$_PYENV_PY")
fi
if command -v python3 >/dev/null 2>&1; then CANDIDATES+=("$(command -v python3)"); fi
CANDIDATES+=("python")

for C in "${CANDIDATES[@]}"; do
  if [[ -x "$C" ]] && "$C" -c "import psycopg" >/dev/null 2>&1; then
    PYTHON_BIN="$C"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  for C in "${CANDIDATES[@]}"; do
    if [[ -x "$C" ]]; then
      PYTHON_BIN="$C"
      break
    fi
  done
fi

# Defaults
DAYS_WINDOW="${DAYS_WINDOW:-1}"               # keep small to avoid search API 500
DOC_CODE="${DOC_CODE:-NS}"
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

# Ensure core dependency exists (helpful on fresh servers)
if ! "$PYTHON_BIN" -c "import psycopg" >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] psycopg missing for $PYTHON_BIN — installing requirements" | tee -a "$LOG_FILE"
  "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel >> "$LOG_FILE" 2>&1 || true
  "$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1 || true
fi

if ! "$PYTHON_BIN" -c "import psycopg" >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] FATAL: psycopg still missing for $PYTHON_BIN" | tee -a "$LOG_FILE"
  exit 1
fi

echo "[$(date '+%F %T')] maricopa one-shot started | days=${DAYS_WINDOW} doc_code=${DOC_CODE} workers=${WORKERS}" | tee -a "$LOG_FILE"
echo "[$(date '+%F %T')] run start" | tee -a "$LOG_FILE"

EXIT_CODE=0
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
  >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
  echo "[$(date '+%F %T')] run success" | tee -a "$LOG_FILE"
else
  echo "[$(date '+%F %T')] run failed exit=$EXIT_CODE" | tee -a "$LOG_FILE"
fi

echo "[$(date '+%F %T')] maricopa one-shot finished" | tee -a "$LOG_FILE"
exit $EXIT_CODE
