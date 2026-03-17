#!/usr/bin/env sh
set -eu

mkdir -p /app/logs /app/output /app/tmp

# Start cron in background
cron

# Start API server in foreground
exec gunicorn -c gunicorn.conf.py maricopa.server:app
