#!/usr/bin/env python3
"""
Live demo: fetch Coconino County recorder docs for the LAST 30 DAYS,
de-duplicate, and display Fee Number / Recording Date / Doc ID / Grantor / Grantee.

Usage:
    python3 demo_last_month.py                  # headless (uses saved Playwright session)
    python3 demo_last_month.py --headful        # open visible browser window
    python3 demo_last_month.py --no-run         # only parse what's already on disk
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
SCRIPT    = Path(__file__).resolve().parent / "fetch_with_session.py"
PYTHON    = sys.executable

# ── date range ────────────────────────────────────────────────────────────────
TODAY      = date.today()
MONTH_AGO  = TODAY - timedelta(days=30)
START_DATE = MONTH_AGO.strftime("%-m/%-d/%Y")   # e.g. "2/11/2026"
END_DATE   = TODAY.strftime("%-m/%-d/%Y")        # e.g. "3/13/2026"

CSV_NAME  = f"demo_{MONTH_AGO.strftime('%Y%m%d')}_{TODAY.strftime('%Y%m%d')}.csv"
JSON_NAME = f"demo_{MONTH_AGO.strftime('%Y%m%d')}_{TODAY.strftime('%Y%m%d')}.json"

TARGET_TYPES = {
    "LIS PENDENS", "TRUSTEES DEED", "SHERIFFS DEED", "TREASURERS DEED",
    "STATE LIEN", "STATE TAX LIEN", "RELEASE STATE TAX LIEN",
}

SEPARATOR = "─" * 110


def run_pipeline(headful: bool = False) -> Path:
    """Run fetch_with_session.py for the last-30-days window."""
    cmd = [
        PYTHON, str(SCRIPT),
        "--start-date", START_DATE,
        "--end-date",   END_DATE,
        "--csv-name",   CSV_NAME,
        "--json-name",  JSON_NAME,
        "--detail-max-records", "100",
        "--ocr-principal-limit", "5",
        "--no-env-cookie",
    ]
    if headful:
        cmd.append("--headful")

    print(f"\n{'='*110}")
    print(f"  🚀  COCONINO COUNTY – LAST 30 DAYS LIVE FETCH")
    print(f"  Date range : {START_DATE}  →  {END_DATE}")
    print(f"  Output CSV : {CSV_NAME}")
    print(f"{'='*110}\n")
    print(f"Running: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=str(OUTPUT_DIR.parent), capture_output=False)
    if result.returncode != 0:
        print("\n❌ Pipeline exited with error. Attempting to read any partial CSV...\n")

    csv_path = OUTPUT_DIR / CSV_NAME
    if not csv_path.exists():
        # Fall back to most recent CSV
        csvs = sorted(OUTPUT_DIR.glob("demo_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if csvs:
            csv_path = csvs[0]
            print(f"⚠️  Using most-recent demo CSV: {csv_path.name}\n")
        else:
            print("❌  No CSV found. Run without --no-run first.")
            sys.exit(1)
    return csv_path


def load_and_deduplicate(csv_path: Path) -> list[dict]:
    """Load CSV rows, deduplicate on documentId, return sorted by recording date desc."""
    rows: list[dict] = []
    seen_ids: set[str] = set()
    duplicates = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            doc_id = (row.get("documentId") or "").strip()
            if not doc_id:
                continue
            if doc_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(doc_id)
            rows.append(row)

    if duplicates:
        print(f"♻️   Deduplicated {duplicates} duplicate document(s) — keeping {len(rows)} unique.\n")
    return rows


def display_table(rows: list[dict]) -> None:
    """Pretty-print the lead table."""
    # Filter to only target document types
    leads = [r for r in rows if r.get("documentType", "").upper().strip() in TARGET_TYPES]
    other = [r for r in rows if r.get("documentType", "").upper().strip() not in TARGET_TYPES]

    print(f"\n{'='*110}")
    print(f"  📋  RESULTS SUMMARY")
    print(f"  Total unique docs fetched : {len(rows)}")
    print(f"  Matching target types     : {len(leads)}")
    print(f"  Other doc types           : {len(other)}")
    print(f"  Target types searched     : {', '.join(sorted(TARGET_TYPES))}")
    print(f"{'='*110}\n")

    if not leads:
        print("⚠️  No matching document types found on this page.")
        print("    The pipeline may only have fetched page 1 (100 rows).")
        print("    Run with --full-pages to fetch all pages.\n")
        # Still show all rows so user can see what was fetched
        leads = rows[:20]

    header = f"{'#':<4} {'FEE/REC #':<12} {'RECORDING DATE':<22} {'DOC ID':<16} {'DOC TYPE':<28} GRANTOR → GRANTEE"
    print(header)
    print(SEPARATOR)

    for i, row in enumerate(leads, 1):
        fee_num   = (row.get("recordingNumber") or "").strip()
        rec_date  = (row.get("recordingDate")   or "").strip()
        doc_id    = (row.get("documentId")       or "").strip()
        doc_type  = (row.get("documentType")     or "").strip()
        grantors  = (row.get("grantors")         or "").strip()
        grantees  = (row.get("grantees")         or "").strip()

        # Truncate long names for display
        gr_from = (grantors[:45] + "…") if len(grantors) > 45 else grantors
        gr_to   = (grantees[:45] + "…") if len(grantees) > 45 else grantees

        print(f"{i:<4} {fee_num:<12} {rec_date:<22} {doc_id:<16} {doc_type:<28} {gr_from}")
        if gr_to:
            print(f"{'':4} {'':12} {'':22} {'':16} {'':28} → {gr_to}")
        print()

    print(SEPARATOR)
    print(f"Total shown: {len(leads)} leads\n")


def show_json_summary(json_path: Path) -> None:
    if not json_path.exists():
        return
    try:
        data = json.loads(json_path.read_text())
        summary = data.get("summary", {})
        print(f"📊  Pipeline JSON summary:")
        print(f"    totalResults  = {summary.get('totalResults', '?')}")
        print(f"    pageCount     = {summary.get('pageCount', '?')}")
        print(f"    filterSummary = {summary.get('filterSummary', '?')}")
        print(f"    recordCount   = {data.get('recordCount', '?')} (detail-enriched on this run)")
        print()
    except Exception:
        pass


def show_cron_instructions() -> None:
    script_abs = str(SCRIPT)
    python_abs = PYTHON

    print(f"\n{'='*110}")
    print("  ⏰  HOW TO SET UP CRON (automated daily run)")
    print(f"{'='*110}")
    print("""
STEP 1 — Edit crontab:
    crontab -e

STEP 2 — Add one of these lines:

  # Run every day at 08:00 AM (last 30 days, headless):
  0 8 * * * /bin/bash -c 'cd {dir} && {py} {sc} --start-date $(date -v-30d +%%-m/%%%-d/%%%%Y) --end-date $(date +%%-m/%%%-d/%%%%Y) --no-env-cookie >> {out}/cron.log 2>&1'

  # Or use the existing shell runner (already has all flags set):
  0 8 * * * /bin/bash {dir}/run_cron.sh >> {out}/cron.log 2>&1

STEP 3 — Verify cron is running:
    tail -f {out}/cron.log

STEP 4 — Check for duplicates across runs:
    The pipeline uses documentId as the unique key.
    The CSV is OVERWRITTEN each run (not appended), so there are never cross-run duplicates.
    If you want to ACCUMULATE across runs, use a PostgreSQL table (already in db_postgres.py)
    with a UNIQUE constraint on documentId.

STEP 5 — View results any time:
    python3 demo_last_month.py --no-run   # re-parse the latest CSV without hitting the network
""".format(
        dir=str(SCRIPT.parent),
        py=python_abs,
        sc=script_abs,
        out=str(OUTPUT_DIR),
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Coconino 30-day demo")
    parser.add_argument("--headful", action="store_true", help="Open visible browser window")
    parser.add_argument("--no-run",  action="store_true", help="Skip the fetch; parse existing CSV only")
    args = parser.parse_args()

    if args.no_run:
        # Find the latest demo CSV
        csvs = sorted(OUTPUT_DIR.glob("demo_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not csvs:
            # fall back to main csv
            csvs = sorted(OUTPUT_DIR.glob("coconino_realtime.csv"))
        if not csvs:
            print("No CSV found. Run without --no-run first.")
            sys.exit(1)
        csv_path = csvs[0]
        print(f"\n📂  Using existing CSV: {csv_path.name}\n")
    else:
        csv_path = run_pipeline(headful=args.headful)

    rows = load_and_deduplicate(csv_path)
    display_table(rows)

    json_path = OUTPUT_DIR / JSON_NAME
    if not json_path.exists():
        json_path = OUTPUT_DIR / "coconino_realtime.json"
    show_json_summary(json_path)
    show_cron_instructions()


if __name__ == "__main__":
    main()
