#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
LOG_FILE="$SCRIPT_DIR/output/cron.log"

mkdir -p "$SCRIPT_DIR/output"

chmod +x "$SCRIPT_DIR/run_coconino_cron.sh"
chmod +x "$SCRIPT_DIR/setup_coconino_cron.sh"

if ! "$PYTHON_BIN" -c "import playwright" >/dev/null 2>&1; then
  echo "[INFO] Installing playwright..."
  "$PYTHON_BIN" -m pip install --user playwright
fi

if ! "$PYTHON_BIN" -m playwright --help >/dev/null 2>&1; then
  echo "[ERROR] Playwright CLI unavailable. Ensure pip user bin is in PATH."
  exit 1
fi

echo "[INFO] Installing chromium browser for playwright..."
"$PYTHON_BIN" -m playwright install chromium

CRON_CMD="*/30 * * * * cd $SCRIPT_DIR && /bin/bash $SCRIPT_DIR/run_coconino_cron.sh >> $LOG_FILE 2>&1 # coconino-realtime-cron"

( crontab -l 2>/dev/null | grep -v 'coconino-realtime-cron' ; echo "$CRON_CMD" ) | crontab -

echo "[OK] Cron installed"
crontab -l | grep 'coconino-realtime-cron' || true

echo "[INFO] Running one immediate cron execution test..."
/bin/bash "$SCRIPT_DIR/run_coconino_cron.sh" | tee "$SCRIPT_DIR/output/manual_cron_test.log"

echo "[DONE] Setup completed"
