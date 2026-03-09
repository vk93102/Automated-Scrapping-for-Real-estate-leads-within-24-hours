#!/usr/bin/env python3
"""
Cron launcher for the Maricopa NS scraper.

Replaces bash file-I/O in run_cron.sh to avoid macOS cron EDEADLK errors.
Python uses C-level file I/O which is unaffected by the bash `read` EDEADLK bug.

Crontab entry (unchanged):
    */10 * * * * /Users/vishaljha/Desktop/web\ scrapping/automation/run_cron.sh
"""

import fcntl
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
DIR     = Path(__file__).parent.resolve()

# In Docker, Python is the system Python (no venv).
# On macOS dev, use the venv Python.
_venv_py = DIR / ".venv" / "bin" / "python"
PY_BIN  = _venv_py if _venv_py.exists() else Path(sys.executable)

LOG_DIR = DIR / "logs"
OUT_DIR = DIR / "output"
TMP_DIR = DIR / "tmp"

for d in (LOG_DIR, OUT_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Single-instance lock (fcntl — works on macOS cron) ────────────────────
LOCKFILE = TMP_DIR / "run_cron.lock"
_lock_fd = open(LOCKFILE, "w")
try:
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    # Another instance is still running — skip silently
    master_log = LOG_DIR / "cron_master.log"
    with open(master_log, "a") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] already running, skipping.\n")
    sys.exit(0)

# ── Load .env (pure Python — no bash read, no EDEADLK) ────────────────────
env = os.environ.copy()
env_file = DIR / ".env"
if env_file.exists():
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env.setdefault(k.strip(), v.strip())   # don't override already-set vars

# GROQ_API_KEY fallback
if not env.get("GROQ_API_KEY") and env.get("LLAMA_API_KEY"):
    env["GROQ_API_KEY"] = env["LLAMA_API_KEY"]

# Fix Tesseract PPM temp path (macOS /tmp symlink vs /private/tmp mismatch)
env["TMPDIR"] = str(TMP_DIR)

# ── Validate ───────────────────────────────────────────────────────────────
db_url = env.get("DATABASE_URL", "")
if not db_url:
    sys.stderr.write("[run_cron.py] ERROR: DATABASE_URL not set\n")
    sys.exit(1)

# ── Dates ──────────────────────────────────────────────────────────────────
yesterday = (date.today() - timedelta(days=1)).isoformat()
log_file  = LOG_DIR / f"cron_{yesterday}.log"

def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(log_file, "a") as f:
        f.write(line)
    # Also write to master log for easy tail -f
    with open(LOG_DIR / "cron_master.log", "a") as f:
        f.write(line)

_log(f"{'='*54}")
_log(f"run_cron.py starting  (yesterday={yesterday})")
_log(f"{'='*54}")

# ── Run scraper ────────────────────────────────────────────────────────────
cmd = [
    str(PY_BIN), "-m", "maricopa_scraper.scraper",
    "--document-code", "NS",
    "--begin-date",    yesterday,
    "--end-date",      yesterday,
    "--limit",         "0",
    "--pdf-mode",      "memory",
    "--sleep",         "0.3",
    "--only-new",
    "--workers",       "5",
    "--db-url",        db_url,
    "--log-level",     "INFO",
    "--out-json",      str(OUT_DIR / f"cron_{yesterday}.json"),
    "--out-csv",       str(OUT_DIR / f"cron_{yesterday}.csv"),
]

with open(log_file, "a") as lf:
    result = subprocess.run(cmd, env=env, stdout=lf, stderr=lf)

exit_code = result.returncode
_log(f"run_cron.py finished (exit={exit_code})")

# ── Cleanup Tesseract PPM temp files ──────────────────────────────────────
for pattern in ("tess_*", "*.PPM", "*.ppm"):
    for f in TMP_DIR.glob(pattern):
        try:
            f.unlink()
        except OSError:
            pass

# Release lock
_lock_fd.close()
try:
    LOCKFILE.unlink()
except OSError:
    pass

sys.exit(exit_code)
