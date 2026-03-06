#!/usr/bin/env bash
set -euo pipefail

# scripts/install_cron.sh
# Safely add a cron entry to run run_pipeline.sh every 10 minutes for testing.
# Usage:
#   ./scripts/install_cron.sh        # preview only
#   ./scripts/install_cron.sh --yes  # perform installation

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/run_pipeline.sh"
LOG_FILE="$SCRIPT_DIR/logs/cron_test.log"

CRON_SCHEDULE="*/10 * * * *"
# The command is wrapped with /bin/bash -lc to handle spaces and environment from .env
CRON_CMD="$CRON_SCHEDULE /bin/bash -lc '\"$RUN_SCRIPT\" >> \"$LOG_FILE\" 2>&1'"

BACKUP="$HOME/crontab_backup_maricopa_$(date +%Y%m%d_%H%M%S).txt"

echo "Backing up existing crontab to: $BACKUP"
crontab -l > "$BACKUP" 2>/dev/null || echo "# empty crontab" > "$BACKUP"

# Show what would be installed
echo
echo "--- Cron entry to install ---"
echo "$CRON_CMD"
echo "-----------------------------"

if [ "${1:-}" != "--yes" ]; then
  echo
  echo "This is a preview only. To install the entry, re-run with --yes"
  exit 0
fi

# Prevent duplicate entries
if crontab -l 2>/dev/null | grep -F -q "$RUN_SCRIPT"; then
  echo "Cron already contains an entry referencing $RUN_SCRIPT. Aborting to avoid duplicates."
  exit 0
fi

# Install
( crontab -l 2>/dev/null; echo "$CRON_CMD" ) | crontab -

echo "Installed. Current crontab entries (filtered):"
crontab -l | grep --color=never -F "$RUN_SCRIPT" || true

echo "Done. Logs will be appended to: $LOG_FILE"
