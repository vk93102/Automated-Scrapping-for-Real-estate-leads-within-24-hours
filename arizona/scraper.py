from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Any

from arizona.apache_assessor import ApacheAssessorClient, ApacheAssessorError
from maricopa.dotenv import load_dotenv_if_present
from maricopa.http_client import RetryConfig
from maricopa.logging_setup import setup_logging
from maricopa.proxies import ProxyProvider


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apache County Assessor real-time scraper")
    parser.add_argument("--dotenv", default=".env", help="Optional .env file")
    parser.add_argument("--base-url", default=os.environ.get("APACHE_ASSESSOR_BASE_URL", "https://eagleassessor.co.apache.az.us"))
    parser.add_argument("--username", default=os.environ.get("APACHE_ASSESSOR_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("APACHE_ASSESSOR_PASSWORD", ""))
    parser.add_argument("--results-url", default="", help="Full saleResults.jsp URL from an authenticated search")
    parser.add_argument("--search-id", default="", help="Server-generated search id for saleResults.jsp")
    parser.add_argument("--search-form-json", default="", help="JSON object to POST to saleSearchPOST.jsp")
    parser.add_argument("--begin-date", default="", help="Timeline mode start date YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="Timeline mode end date YYYY-MM-DD")
    parser.add_argument("--chunk-months", type=int, default=1, help="Number of months per search chunk in timeline mode")
    parser.add_argument("--begin-date-field", default=os.environ.get("APACHE_ASSESSOR_BEGIN_DATE_FIELD", "fromDate"))
    parser.add_argument("--end-date-field", default=os.environ.get("APACHE_ASSESSOR_END_DATE_FIELD", "toDate"))
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--sort", default="Document Type")
    parser.add_argument("--dir", default="asc", choices=["asc", "desc"])
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--no-details", action="store_true", help="Skip account detail fetches and return result rows only")
    parser.add_argument("--timeout-s", type=float, default=float(os.environ.get("APACHE_ASSESSOR_TIMEOUT_S", "30")))
    parser.add_argument("--proxy-list", default=os.environ.get("PROXY_LIST_PATH", "proxy_list.txt"))
    parser.add_argument("--use-proxy", action="store_true", help="Enable proxy rotation for requests")
    parser.add_argument("--out-json", default="output/arizona_latest.json")
    parser.add_argument("--out-csv", default="output/arizona_latest.csv")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def _parse_iso_date(value: str) -> date:
    parts = (value or "").split("-")
    if len(parts) != 3:
        raise ValueError(f"Bad ISO date: {value!r}")
    year, month, day = (int(x) for x in parts)
    return date(year, month, day)


def _first_day_of_month(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(value: date, months: int) -> date:
    month_index = (value.year * 12 + (value.month - 1)) + months
    year = month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, 1)


def _month_windows(begin: date, end: date, chunk_months: int) -> list[tuple[date, date]]:
    chunk_months = max(1, int(chunk_months))
    windows: list[tuple[date, date]] = []
    cursor = _first_day_of_month(begin)
    while cursor <= end:
        next_start = _add_months(cursor, chunk_months)
        window_end = min(end, next_start - timedelta(days=1))
        window_begin = max(begin, cursor)
        windows.append((window_begin, window_end))
        cursor = next_start
    return windows


def _dedupe_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("accountNumber") or "").strip(),
        str(row.get("saleDate") or "").strip(),
        str(row.get("salePrice") or "").strip(),
        str(row.get("requestUrl") or "").strip(),
    )


def _write_property_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "accountNumber",
        "ownerName",
        "granteeName",
        "salePrice",
        "area",
        "saleDate",
        "parcelNumber",
        "propertyAddress",
        "documentType",
        "requestUrl",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "accountNumber",
        "sourceUrl",
        "rowIndex",
        "rowText",
        "documentCount",
        "documentsJson",
        "columnsJson",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "accountNumber": row.get("accountNumber", ""),
                    "sourceUrl": row.get("requestUrl", ""),
                    "rowIndex": row.get("rowIndex", ""),
                    "rowText": row.get("rowText", ""),
                    "documentCount": len(row.get("documents") or []),
                    "documentsJson": json.dumps(row.get("documents") or [], ensure_ascii=False),
                    "columnsJson": json.dumps(row.get("columns") or {}, ensure_ascii=False),
                }
            )


def main() -> None:
    args = _parse_args()
    load_dotenv_if_present(args.dotenv)
    logger = setup_logging(level=args.log_level)

    if not args.username or not args.password:
        raise SystemExit("Missing APACHE_ASSESSOR_USERNAME / APACHE_ASSESSOR_PASSWORD")
    if not args.results_url and not args.search_id and not args.search_form_json and not (args.begin_date and args.end_date):
        raise SystemExit("Pass --results-url, --search-id, --search-form-json, or timeline dates --begin-date/--end-date")

    search_form_data: dict[str, str] | None = None
    if args.search_form_json:
        parsed = json.loads(args.search_form_json)
        if not isinstance(parsed, dict):
            raise SystemExit("--search-form-json must be a JSON object")
        search_form_data = {str(k): "" if v is None else str(v) for k, v in parsed.items()}

    proxies = None
    if args.use_proxy:
        proxy_provider = ProxyProvider.from_file(args.proxy_list)
        proxies = proxy_provider.as_requests_proxies()
        if not proxies:
            logger.warning("--use-proxy was set but no proxy could be loaded from %s", args.proxy_list)

    client = ApacheAssessorClient(
        base_url=args.base_url,
        timeout_s=args.timeout_s,
        proxies=proxies,
        retry=RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=8.0),
    )

    logger.info("Logging into Apache County Assessor at %s", args.base_url)
    client.login(args.username, args.password)

    logger.info(
        "Starting real-time scrape results_url=%s search_id=%s page=%s max_pages=%s",
        bool(args.results_url),
        args.search_id or "(none)",
        args.page,
        args.max_pages,
    )
    timeline_runs: list[dict[str, Any]] = []
    normalized_rows: list[dict[str, Any]] = []

    if args.begin_date and args.end_date:
        begin = _parse_iso_date(args.begin_date)
        end = _parse_iso_date(args.end_date)
        if begin > end:
            raise SystemExit("--begin-date must be before or equal to --end-date")
        if not search_form_data:
            raise SystemExit("Timeline mode requires --search-form-json so date fields can be posted to saleSearchPOST.jsp")

        for window_begin, window_end in _month_windows(begin, end, args.chunk_months):
            window_form = dict(search_form_data)
            window_form[str(args.begin_date_field)] = window_begin.isoformat()
            window_form[str(args.end_date_field)] = window_end.isoformat()
            logger.info("Scraping Arizona property window %s -> %s", window_begin.isoformat(), window_end.isoformat())
            run = client.scrape_sale_results(
                search_form_data=window_form,
                page=args.page,
                page_size=args.page_size,
                sort=args.sort,
                direction=args.dir,
                max_pages=args.max_pages,
                max_records=args.max_records,
                include_details=(not args.no_details),
            )
            run_payload = run.as_dict()
            run_payload["timelineWindow"] = {
                "beginDate": window_begin.isoformat(),
                "endDate": window_end.isoformat(),
            }
            timeline_runs.append(run_payload)
            normalized_rows.extend(run_payload.get("normalizedRecords") or [])
    else:
        run = client.scrape_sale_results(
            results_url=args.results_url or None,
            search_id=args.search_id,
            search_form_data=search_form_data,
            page=args.page,
            page_size=args.page_size,
            sort=args.sort,
            direction=args.dir,
            max_pages=args.max_pages,
            max_records=args.max_records,
            include_details=(not args.no_details),
        )
        run_payload = run.as_dict()
        timeline_runs.append(run_payload)
        normalized_rows.extend(run_payload.get("normalizedRecords") or [])

    deduped_rows: list[dict[str, Any]] = []
    seen = set()
    for row in normalized_rows:
        key = _dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped_rows.append(row)

    payload = {
        "ok": True,
        "county": "Apache",
        "officeName": "Apache County Assessor",
        "source": "Apache County Assessor",
        "timeline": {
            "beginDate": args.begin_date or "",
            "endDate": args.end_date or "",
            "chunkMonths": args.chunk_months,
        },
        "totalRuns": len(timeline_runs),
        "recordCount": len(deduped_rows),
        "properties": deduped_rows,
        "runs": timeline_runs,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_property_csv(args.out_csv, deduped_rows)
    logger.info("Saved %d Arizona assessor property records to %s and %s", len(deduped_rows), args.out_json, args.out_csv)


if __name__ == "__main__":
    try:
        main()
    except ApacheAssessorError as exc:
        logging.getLogger(__name__).error("Arizona assessor scrape failed: %s", exc)
        raise SystemExit(1)
