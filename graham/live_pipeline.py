from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graham.extractor import DEFAULT_DOCUMENT_TYPES, run_graham_pipeline  # noqa: E402


def _default_dates() -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=7)
    return start.strftime("%-m/%-d/%Y"), end.strftime("%-m/%-d/%Y")


def main() -> None:
    dstart, dend = _default_dates()

    parser = argparse.ArgumentParser(description="Graham County, AZ — end-to-end leads pipeline")
    parser.add_argument("--start-date", default=dstart)
    parser.add_argument("--end-date", default=dend)
    parser.add_argument("--doc-types", nargs="+", default=DEFAULT_DOCUMENT_TYPES)
    parser.add_argument("--pages", type=int, default=0)
    parser.add_argument("--ocr-limit", type=int, default=10)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--no-groq", action="store_true")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    res = run_graham_pipeline(
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

    print(f"Records={len(rows)} WithAddress={with_addr} WithAmount={with_amt}")
    print(f"CSV={res.get('csv_path','')}")
    print(f"JSON={res.get('json_path','')}")


if __name__ == "__main__":
    main()
