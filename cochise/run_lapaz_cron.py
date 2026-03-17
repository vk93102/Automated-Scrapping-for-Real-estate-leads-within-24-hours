#!/usr/bin/env python3
"""Cron runner for Greenlee foreclosure pipeline (every 15 minutes)."""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Resolve project root and import pipeline
GREENLEE_DIR = Path(__file__).resolve().parent
ROOT_DIR = GREENLEE_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from greenlee.extractor import run_greenlee_pipeline  # noqa: E402

LOG_DIR = ROOT_DIR / "logs"
TMP_DIR = ROOT_DIR / "tmp"
OUT_DIR = GREENLEE_DIR / "output"

for d in (LOG_DIR, TMP_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

LOCKFILE = TMP_DIR / "greenlee_cron.lock"
lock_fd = open(LOCKFILE, "w")


def _log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
    with open(LOG_DIR / "greenlee_cron.log", "a", encoding="utf-8") as f:
        f.write(line)


try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    _log("already running; skipping")
    sys.exit(0)

# Load .env if present
env_file = ROOT_DIR / ".env"
if env_file.exists():
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

# Last 7 days window
end = date.today()
start = end - timedelta(days=7)

doc_types = [
    "NOTICE OF DEFAULT",
    "NOTICE OF TRUSTEE SALE",
    "LIS PENDENS",
    "DEED IN LIEU",
    "TREASURERS DEED",
    "NOTICE OF REINSTATEMENT",
]

_log("greenlee cron started")
_log(f"date range: {start:%-m/%-d/%Y} -> {end:%-m/%-d/%Y}")

try:
    res = run_greenlee_pipeline(
        start_date=start.strftime("%-m/%-d/%Y"),
        end_date=end.strftime("%-m/%-d/%Y"),
        doc_types=doc_types,
        max_pages=0,
        ocr_limit=0,
        workers=3,
        use_groq=True,
        headless=True,
        verbose=False,
    )

    csv_path = Path(res.get("csv_path", ""))
    json_path = Path(res.get("json_path", ""))

    # Stable latest files for sheet ingestion / downstream jobs
    latest_csv = OUT_DIR / "greenlee_latest.csv"
    latest_json = OUT_DIR / "greenlee_latest.json"
    if csv_path.exists():
        shutil.copyfile(csv_path, latest_csv)
    if json_path.exists():
        shutil.copyfile(json_path, latest_json)

    # Daily append file (optional history sheet)
    daily_csv = OUT_DIR / f"greenlee_daily_{date.today():%Y%m%d}.csv"
    if csv_path.exists():
        shutil.copyfile(csv_path, daily_csv)

    count = len(res.get("records", []))
    _log(f"completed ok: records={count}")
    _log(f"csv={csv_path}")
    _log(f"latest_csv={latest_csv}")
except Exception as e:
    _log(f"failed: {e}")
    sys.exit(1)
finally:
    lock_fd.close()
    try:
        LOCKFILE.unlink()
    except OSError:
        pass
