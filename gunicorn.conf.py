"""
gunicorn.conf.py — Production server configuration for Maricopa Recorder Scraper.

IMPORTANT — workers must stay at 1
  Job state is tracked in an in-memory dict (_jobs). Multiple workers would each
  have their own dict, causing GET /api/v1/jobs/{id} to 404 on jobs started by a
  different worker.  Migrate job state to Supabase before increasing workers.

Usage:
  # foreground (dev)
  gunicorn -c gunicorn.conf.py maricopa_scraper.server:app

  # daemon (production)
  DAEMON=1 PRODUCTION=1 ./run_server.sh
"""
import os

# ── Binding ────────────────────────────────────────────────────────────────
bind = os.environ.get("BIND", f"0.0.0.0:{os.environ.get('PORT', '8080')}")

# ── Workers ────────────────────────────────────────────────────────────────
# Must stay 1 — see note above.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"

# Threads allow concurrent request handling within the single worker.
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# ── Timeouts ───────────────────────────────────────────────────────────────
# Scraping jobs can run for up to an hour; keep timeout generous.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "3600"))
graceful_timeout = 30
keepalive = 5

# ── Logging ────────────────────────────────────────────────────────────────
# Log to stdout/stderr so systemd / Docker / Railway can capture them.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sµs'
