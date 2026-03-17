#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SCRIPT="$ROOT/cochise/run_cochise_cron.sh"
LOG_FILE="$ROOT/logs/cochise_cron.log"
CRON_CMD="*/15 * * * * /bin/bash -lc '\"$RUN_SCRIPT\" >> \"$LOG_FILE\" 2>&1'"
BACKUP="$HOME/crontab_backup_cochise_$(date +%Y%m%d_%H%M%S).txt"
crontab -l > "$BACKUP" 2>/dev/null || echo "# empty crontab" > "$BACKUP"
echo "$CRON_CMD"
if [ "${1:-}" != "--yes" ]; then
  echo "Preview only. Re-run with --yes to install."
  exit 0
fi
mkdir -p "$ROOT/logs" "$ROOT/tmp" "$ROOT/cochise/output"
chmod +x "$RUN_SCRIPT" "$ROOT/cochise/run_cochise_cron.py"
if crontab -l 2>/dev/null | grep -F -q "$RUN_SCRIPT"; then
  echo "Existing Cochise cron entry found."
  exit 0
fi
( crontab -l 2>/dev/null; echo "$CRON_CMD" ) | crontab -
crontab -l | grep -F "$RUN_SCRIPT" || true
