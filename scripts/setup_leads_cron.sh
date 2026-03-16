#!/usr/bin/env bash
# =============================================================================
# scripts/setup_leads_cron.sh — Install */15 cron job for daily lead scraping
#
# Usage:
#   bash scripts/setup_leads_cron.sh           # install (both counties)
#   bash scripts/setup_leads_cron.sh remove    # remove the cron entry
#   bash scripts/setup_leads_cron.sh show      # print current crontab
# =============================================================================

set -euo pipefail

PY="/Users/vishaljha/.pyenv/versions/3.10.13/bin/python"
REPO="/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
SCRIPT="$REPO/run_daily_leads.py"
LOG="$REPO/logs/leads_cron_master.log"

CRON_ENTRY="*/15 * * * * $PY $SCRIPT >> $LOG 2>&1"
CRON_MARKER="run_daily_leads"

action="${1:-install}"

case "$action" in

  remove)
    echo "[CRON] Removing '$CRON_MARKER' entries from crontab …"
    ( crontab -l 2>/dev/null | grep -v "$CRON_MARKER" ) | crontab -
    echo "[CRON] Done. Current crontab:"
    crontab -l 2>/dev/null || echo "  (empty)"
    ;;

  show)
    echo "[CRON] Current crontab:"
    crontab -l 2>/dev/null || echo "  (empty)"
    ;;

  install|*)
    echo "[CRON] Installing cron entry …"
    echo "[CRON] Entry: $CRON_ENTRY"
    echo ""

    # Verify Python and script exist
    if [[ ! -x "$PY" ]]; then
      echo "❌ Python not found at $PY"; exit 1
    fi
    if [[ ! -f "$SCRIPT" ]]; then
      echo "❌ Script not found at $SCRIPT"; exit 1
    fi

    # Create logs directory
    mkdir -p "$REPO/logs"

    # Add entry only if not already present
    (
      crontab -l 2>/dev/null | grep -v "$CRON_MARKER"
      echo "$CRON_ENTRY"
    ) | crontab -

    echo ""
    echo "✓ Cron job installed. Current crontab:"
    crontab -l | grep "$CRON_MARKER" | sed 's/^/  /'
    echo ""
    echo "Useful commands:"
    echo "  tail -f $LOG"
    echo "  tail -f $REPO/logs/leads_\$(date +%Y-%m-%d).log"
    echo "  tail -f $REPO/logs/coconino_\$(date +%Y-%m-%d).log"
    echo "  tail -f $REPO/logs/gila_\$(date +%Y-%m-%d).log"
    echo ""
    echo "To remove:  bash scripts/setup_leads_cron.sh remove"
    ;;

esac
