#!/usr/bin/env python3
"""
Gila County, AZ — Real Estate Lead Scraper
================================================
Entry point for the Gila County pipeline.

This script is a lightweight wrapper around the shared `greenlee` extractor,
configured for Gila County's specifics. It handles command-line argument
parsing and invokes the main pipeline logic.

For core scraping, OCR, and LLM logic, see `greenlee/extractor.py`.
For Gila-specific configurations, see `gila/extractor.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Allow running as: python3 gila/live_pipeline.py
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(THIS_DIR.parent))

from gila import extractor


def main():
    """CLI entry point for Gila County pipeline."""
    parser = argparse.ArgumentParser(
        description="Run Gila County, AZ real estate lead scraping pipeline."
    )
    parser.add_argument(
        "--start-date",
        help="Start date (YYYY-MM-DD)",
        default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    parser.add_argument(
        "--end-date",
        help="End date (YYYY-M-DD)",
        default=datetime.now().strftime("%Y-%m-%d"),
    )
    parser.add_argument(
        "--doc-types",
        nargs="+",
        help="List of document types to search for",
        default=extractor.DEFAULT_DOCUMENT_TYPES,
    )
    parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Use Playwright for initial session acquisition (slower but more robust)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run Playwright in headless mode (no browser UI)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_false",
        dest="headless",
        help="Run Playwright with a visible browser UI for debugging",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=0,
        help="Maximum number of result pages to fetch (0 for all)",
    )
    parser.add_argument(
        "--record-limit",
        type=int,
        default=0,
        help="Maximum number of records to process (0 for all)",
    )
    parser.add_argument(
        "--no-groq",
        action="store_true",
        help="Skip Groq analysis (OCR only)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    # The Gila pipeline inherits from the Greenlee base.
    # All logic is in `greenlee.extractor` and configured in `gila.extractor`.
    results = extractor.run_gila_pipeline(
        start_date=args.start_date,
        end_date=args.end_date,
        doc_types=args.doc_types,
        headless=args.headless,
        max_pages=args.page_limit,
        ocr_limit=args.record_limit,
        use_groq=not args.no_groq,
        verbose=args.verbose,
    )

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
