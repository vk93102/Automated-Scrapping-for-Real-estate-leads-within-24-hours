#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CRON_LOG="$ROOT_DIR/logs/cron_maricopa.log"
JOB="*/20 * * * * /bin/bash $ROOT_DIR/cron/maricopa.sh >> $CRON_LOG 2>&1"

mkdir -p "$ROOT_DIR/logs"
chmod +x "$ROOT_DIR/run_pipeline.sh" "$ROOT_DIR/cron/maricopa.sh"

TMP_CRON="$(mktemp)"
crontab -l 2>/dev/null | grep -v "$ROOT_DIR/cron/maricopa.sh" > "$TMP_CRON" || true
echo "$JOB" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm -f "$TMP_CRON"

echo "Installed cron job:"
crontab -l | grep "$ROOT_DIR/cron/maricopa.sh" || true
