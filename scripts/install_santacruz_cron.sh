#!/usr/bin/env bash
set -euo pipefail

# Install Santa Cruz cron every 15 minutes.
# Usage:
#   ./scripts/install_santacruz_cron.sh        # preview
#   ./scripts/install_santacruz_cron.sh --yes  # install

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SCRIPT="$ROOT/SANTA CRUZ/run_santacruz_cron.sh"
LOG_FILE="$ROOT/logs/santacruz_cron.log"
CRON_SCHEDULE="*/15 * * * *"
CRON_CMD="$CRON_SCHEDULE /bin/bash -lc '\"$RUN_SCRIPT\" >> \"$LOG_FILE\" 2>&1'"

BACKUP="$HOME/crontab_backup_santacruz_$(date +%Y%m%d_%H%M%S).txt"
crontab -l > "$BACKUP" 2>/dev/null || echo "# empty crontab" > "$BACKUP"

echo "Backed up current crontab to: $BACKUP"
echo
echo "--- Santa Cruz cron entry ---"
echo "$CRON_CMD"
echo "-----------------------------"

if [ "${1:-}" != "--yes" ]; then
  echo
  echo "Preview only. Re-run with --yes to install."
  exit 0
fi

mkdir -p "$ROOT/logs" "$ROOT/tmp" "$ROOT/SANTA CRUZ/output"
chmod +x "$RUN_SCRIPT" "$ROOT/SANTA CRUZ/run_santacruz_cron.py"

if crontab -l 2>/dev/null | grep -F -q "$RUN_SCRIPT"; then
  echo "Existing Santa Cruz cron entry found. Skipping duplicate install."
  exit 0
fi

( crontab -l 2>/dev/null; echo "$CRON_CMD" ) | crontab -

echo "Installed. Active matching cron entries:"
crontab -l | grep -F "$RUN_SCRIPT" || true
echo "Log file: $LOG_FILE"
echo "Latest CSV path: $ROOT/SANTA CRUZ/output/santacruz_latest.csv"
