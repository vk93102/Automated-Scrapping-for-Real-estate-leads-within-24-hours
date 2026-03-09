#!/bin/bash
# docker-entrypoint.sh
# Starts cron daemon (for the 10-min scraper) + gunicorn API server.
set -e

# Create runtime dirs in case volume mounts wiped them
mkdir -p /app/logs /app/output /app/tmp

# Start cron daemon in background
service cron start

echo "[entrypoint] cron started"
echo "[entrypoint] starting gunicorn API on 0.0.0.0:${PORT:-8080}"

# Run the API server (foreground — Docker needs PID 1 to be the main process)
exec gunicorn -c gunicorn.conf.py maricopa_scraper.server:app
