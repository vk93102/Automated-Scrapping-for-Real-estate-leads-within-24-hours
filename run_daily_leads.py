#!/usr/bin/env python3
"""
run_daily_leads.py — Daily cron runner: Coconino + Gila County lead scrapers.
==============================================================================

Designed to be fired every 15 minutes by cron.
Each invocation fetches the last 7 days of recordings for both counties.
A single-instance lock (fcntl) ensures overlapping runs are skipped silently.

Crontab entry (installed by scripts/setup_leads_cron.sh):
    */15 * * * * /Users/vishaljha/.pyenv/versions/3.10.13/bin/python \
        /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/run_daily_leads.py \
        >> /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/leads_cron_master.log 2>&1

Usage (manual):
    python run_daily_leads.py                        # last 7 days (default)
    python run_daily_leads.py --start-date 3/9/2026 --end-date 3/15/2026
    python run_daily_leads.py --county gila
    python run_daily_leads.py --county coconino
    python run_daily_leads.py --no-ocr               # metadata only, fastest
    python run_daily_leads.py --verbose
"""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
DIR     = Path(__file__).parent.resolve()
PY_BIN  = Path(sys.executable)           # same interpreter that launched us
LOG_DIR = DIR / "logs"
TMP_DIR = DIR / "tmp"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
_RUN_TS  = datetime.now().strftime("%Y-%m-%d")
_LOG     = LOG_DIR / f"leads_{_RUN_TS}.log"
_MASTER  = LOG_DIR / "leads_cron_master.log"


def log(msg: str, *, also_stdout: bool = True) -> None:
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    if also_stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    with open(_LOG, "a") as f:
        f.write(line)
    with open(_MASTER, "a") as f:
        f.write(line)


# ── Single-instance lock ──────────────────────────────────────────────────────
LOCKFILE = TMP_DIR / "run_daily_leads.lock"
_lock_fd = open(LOCKFILE, "w")
try:
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    log("Another instance is already running — skipping this invocation.", also_stdout=False)
    sys.exit(0)


# ── Load .env into environment ────────────────────────────────────────────────
def _load_env() -> dict[str, str]:
    env      = os.environ.copy()
    env_file = DIR / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip())
    env["TMPDIR"] = str(TMP_DIR)   # Fix Tesseract PPM path on macOS
    return env


# ── Run one county pipeline ───────────────────────────────────────────────────
def run_county(
    label:      str,
    script:     str,           # relative to DIR, e.g. "conino/live_pipeline.py"
    start_date: str,
    end_date:   str,
    ocr_limit:  int,
    use_groq:   bool,
    extra_args: list[str],
    env:        dict[str, str],
    verbose:    bool,
) -> int:
    """Spawn the county pipeline subprocess; tee output to its own log."""
    county_log = LOG_DIR / f"{label.lower()}_{_RUN_TS}.log"
    script_abs = DIR / script

    cmd: list[str] = [
        str(PY_BIN), str(script_abs),
        "--start-date", start_date,
        "--end-date",   end_date,
        "--ocr-limit",  str(ocr_limit),
    ]
    if not use_groq:
        cmd.append("--no-groq")
    if verbose and "--verbose" not in extra_args:
        cmd.append("--verbose")
    cmd.extend(extra_args)

    log(f"[{label}] ─── Pipeline start ───────────────────────────────")
    log(f"[{label}] Date  : {start_date} → {end_date}")
    log(f"[{label}] OCR   : {'all' if ocr_limit == 0 else ('skip' if ocr_limit == -1 else str(ocr_limit))}")
    log(f"[{label}] Groq  : {'yes' if use_groq else 'no'}")
    log(f"[{label}] CMD   : {' '.join(cmd)}")

    t0 = time.time()
    try:
        with open(county_log, "a") as lf:
            header = (
                f"\n{'='*70}\n"
                f"[{datetime.now()}] {label} START  {start_date} → {end_date}\n"
                f"{'='*70}\n"
            )
            lf.write(header)
            lf.flush()

            proc = subprocess.Popen(
                cmd, env=env, cwd=str(DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            # Tee subprocess stdout → both the county log AND our own stdout
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(f"  [{label}] {line}")
                sys.stdout.flush()
                lf.write(line)

            proc.wait()
            exit_code = proc.returncode

    except Exception as exc:
        log(f"[{label}] ❌ Subprocess error: {exc}")
        exit_code = 1

    elapsed = time.time() - t0
    status  = "✓ OK" if exit_code == 0 else f"✗ FAILED (exit={exit_code})"
    log(f"[{label}] {status}  elapsed={elapsed:.0f}s  log={county_log.name}")

    # Print most recent CSV produced by this county
    if exit_code == 0:
        _show_latest_csv(label, script)

    return exit_code


def _show_latest_csv(label: str, script: str) -> None:
    """Find and summarise the most recently written CSV for this county."""
    county_out = DIR / Path(script).parent / "output"
    pattern    = "coconino_pipeline_*.csv" if "conino" in script else "gila_*.csv"
    csvs = sorted(county_out.glob(pattern)) if county_out.exists() else []
    if not csvs:
        log(f"[{label}] ⚠  No CSV found in {county_out}")
        return
    latest = csvs[-1]
    try:
        import csv
        rows = list(csv.DictReader(open(latest, encoding="utf-8")))
        log(f"[{label}] 📄 CSV: {latest.name}  ({len(rows)} rows)")
        for i, r in enumerate(rows[:5], 1):
            addr = (r.get("propertyAddress") or "").replace("\n", " ")[:50]
            log(f"[{label}]   {i}. {r.get('documentId','')}  {r.get('documentType','')[:20]}  {addr}")
        if len(rows) > 5:
            log(f"[{label}]   … {len(rows) - 5} more rows")
    except Exception as exc:
        log(f"[{label}] ⚠  Could not read CSV: {exc}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    today      = datetime.now()
    last_week  = today - timedelta(days=7)
    p = argparse.ArgumentParser(
        description="Daily Coconino + Gila County lead scraper (cron runner)"
    )
    p.add_argument("--start-date", default=last_week.strftime("%-m/%-d/%Y"),
                   help="Recording date start MM/DD/YYYY (default: 7 days ago)")
    p.add_argument("--end-date",   default=today.strftime("%-m/%-d/%Y"),
                   help="Recording date end   MM/DD/YYYY (default: today)")
    p.add_argument("--county",    choices=["both", "coconino", "gila"], default="both",
                   help="Which county to run (default: both)")
    p.add_argument("--ocr-limit", type=int, default=0,
                   help="OCR cap: 0=all (default), -1=skip, N=cap at N")
    p.add_argument("--no-groq",   action="store_true",
                   help="Disable Groq LLM (use regex-only OCR)")
    p.add_argument("--no-ocr",    action="store_true",
                   help="Skip OCR entirely (metadata only, fastest)")
    p.add_argument("--verbose",   action="store_true")
    return p.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    args = _parse_args()
    env  = _load_env()

    start_date = args.start_date
    end_date   = args.end_date
    ocr_limit  = -1 if args.no_ocr else args.ocr_limit
    use_groq   = not args.no_groq and bool(env.get("GROQ_API_KEY", ""))

    log("=" * 70)
    log(f"run_daily_leads.py  START")
    log(f"  Date  : {start_date} → {end_date}")
    log(f"  County: {args.county}")
    log(f"  OCR   : {'skip' if ocr_limit == -1 else ('all' if ocr_limit == 0 else str(ocr_limit))}")
    log(f"  Groq  : {'enabled' if use_groq else 'disabled'}")
    log(f"  Python: {PY_BIN}")
    log("=" * 70)

    results: dict[str, int] = {}

    if args.county in ("both", "coconino"):
        results["COCONINO"] = run_county(
            label      = "COCONINO",
            script     = "conino/live_pipeline.py",
            start_date = start_date,
            end_date   = end_date,
            ocr_limit  = ocr_limit,
            use_groq   = use_groq,
            extra_args = [],
            env        = env,
            verbose    = args.verbose,
        )

    if args.county in ("both", "gila"):
        results["GILA"] = run_county(
            label      = "GILA",
            script     = "gila/live_pipeline.py",
            start_date = start_date,
            end_date   = end_date,
            ocr_limit  = ocr_limit,
            use_groq   = use_groq,
            extra_args = [],
            env        = env,
            verbose    = args.verbose,
        )

    log("=" * 70)
    for county, rc in results.items():
        status = "✓ SUCCESS" if rc == 0 else f"✗ FAILED (exit={rc})"
        log(f"  {county:<12}: {status}")
    overall = max(results.values()) if results else 0
    log(f"run_daily_leads.py  DONE  (overall exit={overall})")
    log("=" * 70)

    # Release lock
    _lock_fd.close()
    try:
        LOCKFILE.unlink()
    except OSError:
        pass

    sys.exit(overall)


if __name__ == "__main__":
    main()
