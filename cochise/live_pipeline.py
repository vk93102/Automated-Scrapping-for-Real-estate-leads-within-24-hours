from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cochise.extractor import DEFAULT_DOCUMENT_TYPES, run_cochise_pipeline  # noqa: E402


def _default_dates() -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=7)
    return start.strftime("%-m/%-d/%Y"), end.strftime("%-m/%-d/%Y")


def main() -> None:
    dstart, dend = _default_dates()

    parser = argparse.ArgumentParser(
        description="Cochise County, AZ — end-to-end leads pipeline"
    )
    parser.add_argument("--start-date", default=dstart, help=f"MM/DD/YYYY (default: {dstart})")
    parser.add_argument("--end-date", default=dend, help=f"MM/DD/YYYY (default: {dend})")
    parser.add_argument("--doc-types", nargs="+", default=DEFAULT_DOCUMENT_TYPES, help="Document types")
    parser.add_argument("--pages", type=int, default=0, help="Max result pages per doc type (0=all)")
    parser.add_argument("--ocr-limit", type=int, default=10, help="OCR limit: -1 skip, 0 all, N first N")
    parser.add_argument("--workers", type=int, default=3, help="Parallel enrichment workers")
    parser.add_argument("--no-groq", action="store_true", help="Disable Groq extraction")
    parser.add_argument("--headful", action="store_true", help="Run visible browser")
    parser.add_argument("--verbose", action="store_true", help="Verbose logs")

    args = parser.parse_args()

    print("\n============================================================")
    print(" COCHISE COUNTY AZ — REAL ESTATE LEADS PIPELINE")
    print("============================================================")
    print(f" Date Range : {args.start_date} -> {args.end_date}")
    print(f" Doc Types  : {len(args.doc_types)}")

    res = run_cochise_pipeline(
        start_date=args.start_date,
        end_date=args.end_date,
        doc_types=args.doc_types,
        max_pages=args.pages,
        ocr_limit=args.ocr_limit,
        workers=args.workers,
        use_groq=not args.no_groq,
        headless=not args.headful,
        verbose=args.verbose,
    )

    rows = res.get("records", [])
    with_addr = sum(1 for r in rows if r.get("propertyAddress"))
    with_amt = sum(1 for r in rows if r.get("principalAmount"))

    print("\n---------------- RESULT ----------------")
    print(f" Records      : {len(rows)}")
    print(f" With Address : {with_addr}")
    print(f" With Amount  : {with_amt}")
    print(f" CSV          : {res.get('csv_path','')}")
    print(f" JSON         : {res.get('json_path','')}")
    print("----------------------------------------")


if __name__ == "__main__":
    main()
