#!/usr/bin/env bash
set -euo pipefail

# Cron / launchd often run with a minimal PATH.
export PATH="${PATH:-/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

cd "$(dirname "$0")"

# Load environment (DATABASE_URL, API_TOKEN, etc.)
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

PY_BIN="${PYTHON_BIN:-}"
if [[ -z "$PY_BIN" ]]; then
  if [[ -x "./.venv/bin/python" ]]; then
    PY_BIN="./.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
  else
    PY_BIN="python"
  fi
fi

GUNICORN_BIN="${GUNICORN_BIN:-}"
if [[ -z "$GUNICORN_BIN" ]]; then
  if [[ -x "./.venv/bin/gunicorn" ]]; then
    GUNICORN_BIN="./.venv/bin/gunicorn"
  elif command -v gunicorn >/dev/null 2>&1; then
    GUNICORN_BIN="gunicorn"
  fi
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
PID_FILE="logs/server.pid"

_port_in_use() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }
_pid_running()  { [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" >/dev/null 2>&1; }

# ── Daemon helpers ─────────────────────────────────────────────────────────
if [[ "${DAEMON:-}" != "" ]]; then
  mkdir -p logs

  if _pid_running; then
    if [[ "${RESTART:-}" != "" ]]; then
      kill "$(cat "$PID_FILE")" || true
      sleep 1
    else
      echo "server already running (pid $(cat "$PID_FILE"))"
      exit 0
    fi
  fi

  if _port_in_use; then
    echo "port $PORT already in use; set RESTART=1 or change PORT"
    exit 1
  fi

  if [[ "${PRODUCTION:-}" != "" ]]; then
    # ── Production mode: gunicorn + uvicorn worker ─────────────────────────
    if [[ -z "$GUNICORN_BIN" ]]; then
      echo "gunicorn not found — install it: pip install gunicorn"
      exit 1
    fi
    echo "[run_server.sh] starting production server (gunicorn) on $HOST:$PORT"
    nohup "$GUNICORN_BIN" \
      -c gunicorn.conf.py \
      --bind "$HOST:$PORT" \
      automation.maricopa_scraper.server:app \
      > logs/server.log 2>&1 &
  else
    # ── Dev mode: plain uvicorn ─────────────────────────────────────────────
    echo "[run_server.sh] starting dev server (uvicorn) on $HOST:$PORT"
    nohup "$PY_BIN" -m uvicorn \
      automation.maricopa_scraper.server:app \
      --host "$HOST" --port "$PORT" \
      > logs/server.log 2>&1 &
  fi

  echo $! > "$PID_FILE"
  echo "[run_server.sh] server pid=$(cat "$PID_FILE") — logs -> logs/server.log"
  echo "[run_server.sh] API docs: http://$HOST:$PORT/docs"
  exit 0
fi

# ── Foreground mode ────────────────────────────────────────────────────────
if [[ "${PRODUCTION:-}" != "" ]]; then
  if [[ -z "$GUNICORN_BIN" ]]; then
    echo "gunicorn not found — install it: pip install gunicorn"
    exit 1
  fi
  echo "[run_server.sh] production mode — http://$HOST:$PORT/docs"
  exec "$GUNICORN_BIN" \
    -c gunicorn.conf.py \
    --bind "$HOST:$PORT" \
    automation.maricopa_scraper.server:app
else
  echo "[run_server.sh] dev mode — http://$HOST:$PORT/docs"
  exec "$PY_BIN" -m uvicorn \
    automation.maricopa_scraper.server:app \
    --host "$HOST" --port "$PORT" --reload
fi
