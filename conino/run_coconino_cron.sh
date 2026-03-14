#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p "$SCRIPT_DIR/output"

PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

# Auto-compute last-30-days window if dates are not explicitly set.
# macOS date uses -v; GNU date uses --date.
if [[ -z "${COCONINO_START_DATE:-}" ]]; then
  if date --version >/dev/null 2>&1; then
    START_DATE="$(date --date='30 days ago' '+%-m/%-d/%Y')"
  else
    START_DATE="$(date -v-30d '+%-m/%-d/%Y')"
  fi
else
  START_DATE="$COCONINO_START_DATE"
fi

if [[ -z "${COCONINO_END_DATE:-}" ]]; then
  END_DATE="$(date '+%-m/%-d/%Y')"
else
  END_DATE="$COCONINO_END_DATE"
fi

DETAIL_MAX_RECORDS="${COCONINO_DETAIL_MAX_RECORDS:-100}"
OCR_PRINCIPAL_LIMIT="${COCONINO_OCR_PRINCIPAL_LIMIT:-10}"
HEADFUL_FLAG=""
NO_ENV_COOKIE_FLAG=""

# Document types to filter — can override via env: COCONINO_DOCUMENT_TYPES="LIS PENDENS TRUSTEES DEED"
DOCUMENT_TYPES="${COCONINO_DOCUMENT_TYPES:-LIS PENDENS TRUSTEES DEED SHERIFFS DEED TREASURERS DEED STATE LIEN STATE TAX LIEN RELEASE STATE TAX LIEN}"
# Convert to --document-types flags (each type as a quoted argument)
DOC_TYPE_FLAGS=()
while IFS= read -r dtype; do
  [[ -n "$dtype" ]] && DOC_TYPE_FLAGS+=("$dtype")
done < <(echo "$DOCUMENT_TYPES" | tr '\n' '\n' | grep -v '^$' || true)

if [[ "${COCONINO_HEADFUL:-false}" == "true" ]]; then
  HEADFUL_FLAG="--headful"
fi

if [[ "${COCONINO_USE_ENV_COOKIE:-true}" != "true" ]]; then
  NO_ENV_COOKIE_FLAG="--no-env-cookie"
fi

if [[ -z "${COCONINO_COOKIE:-}" && ! -f "$SCRIPT_DIR/output/session_state.json" ]]; then
  echo "[WARN] No COCONINO_COOKIE and no session_state.json yet. First run may require interactive acceptance."
fi

echo "[INFO] Date range: $START_DATE → $END_DATE"
echo "[INFO] Document types: $DOCUMENT_TYPES"

"$PYTHON_BIN" "$SCRIPT_DIR/fetch_with_session.py" \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --csv-name "coconino_realtime.csv" \
  --json-name "coconino_realtime.json" \
  --detail-max-records "$DETAIL_MAX_RECORDS" \
  --ocr-principal-limit "$OCR_PRINCIPAL_LIMIT" \
  $NO_ENV_COOKIE_FLAG \
  $HEADFUL_FLAG
