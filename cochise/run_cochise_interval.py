#!/usr/bin/env python3
"""Cochise interval runner: fetch last N days and upsert records to DB."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import psycopg

COUNTY_DIR = Path(__file__).resolve().parent
ROOT_DIR = COUNTY_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

from county_doc_types import UNIFIED_LEAD_DOC_TYPES
from cochise.extractor import run_cochise_pipeline  # noqa: E402


def _load_env() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _log(msg: str) -> None:
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with (log_dir / "cochise_interval.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    host = (urlparse(u).hostname or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def _connect_db(database_url: str, retries: int = 3, sleep_s: int = 3) -> psycopg.Connection:
    last_err: Exception | None = None
    primary_url = _db_url_with_ssl(database_url)
    fallback_raw = (os.environ.get("DATABASE_URL_POOLER") or "").strip()
    fallback_url = _db_url_with_ssl(fallback_raw) if fallback_raw else ""

    host = (urlparse(primary_url).hostname or "").strip()
    if host:
        try:
            socket.getaddrinfo(host, 5432)
        except Exception as exc:
            if not fallback_url:
                raise RuntimeError(f"DB host DNS resolution failed for {host}: {exc}")
            _log(f"primary DB host DNS failed ({host}): {exc}; trying DATABASE_URL_POOLER")

    for url in [u for u in [primary_url, fallback_url] if u]:
        for i in range(max(1, retries)):
            try:
                return psycopg.connect(url, connect_timeout=12)
            except Exception as exc:
                last_err = exc
                if i < retries - 1:
                    time.sleep(sleep_s)

    raise RuntimeError(f"DB connect failed after {retries} attempts: {last_err}")


def _ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists cochise_leads (
              id               bigserial primary key,
              source_county    text not null default 'Cochise',
              document_id      text not null,
              recording_number text,
              recording_date   text,
              document_type    text,
              grantors         text,
              grantees         text,
              trustor          text,
              trustee          text,
              beneficiary      text,
              principal_amount text,
              property_address text,
              detail_url       text,
              image_urls       text,
              ocr_method       text,
              ocr_chars        integer,
              used_groq        boolean,
              groq_model       text,
              groq_error       text,
              analysis_error   text,
              run_date         date,
              raw_record       jsonb not null default '{}'::jsonb,
              created_at       timestamptz not null default now(),
              updated_at       timestamptz not null default now(),
              unique (source_county, document_id)
            );
            """
        )
        cur.execute(
            """
            create table if not exists cochise_pipeline_runs (
              id              bigserial primary key,
              run_started_at  timestamptz not null default now(),
              run_finished_at timestamptz,
              run_date        date,
              total_records   integer default 0,
              inserted_rows   integer default 0,
              updated_rows    integer default 0,
              llm_used_rows   integer default 0,
              status          text not null default 'running',
              error_message   text,
              created_at      timestamptz not null default now()
            );
            """
        )
    conn.commit()


def _upsert_records(conn: psycopg.Connection, records: list[dict], run_date: date) -> tuple[int, int, int]:
    inserted = 0
    updated = 0
    llm_used = 0
    with conn.cursor() as cur:
        for r in records:
            doc_id = str(r.get("documentId", "") or "").strip()
            if not doc_id:
                continue
            used_groq = bool(r.get("usedGroq", False))
            if used_groq:
                llm_used += 1
            payload = {
                "source_county": r.get("sourceCounty") or "Cochise",
                "document_id": doc_id,
                "recording_number": r.get("recordingNumber", ""),
                "recording_date": r.get("recordingDate", ""),
                "document_type": r.get("documentType", ""),
                "grantors": r.get("grantors", ""),
                "grantees": r.get("grantees", ""),
                "trustor": r.get("trustor", ""),
                "trustee": r.get("trustee", ""),
                "beneficiary": r.get("beneficiary", ""),
                "principal_amount": r.get("principalAmount", ""),
                "property_address": r.get("propertyAddress", ""),
                "detail_url": r.get("detailUrl", ""),
                "image_urls": r.get("imageUrls", ""),
                "ocr_method": r.get("ocrMethod", ""),
                "ocr_chars": int(r.get("ocrChars") or 0),
                "used_groq": used_groq,
                "groq_model": r.get("groqModel", ""),
                "groq_error": r.get("groqError", ""),
                "analysis_error": r.get("analysisError", ""),
                "run_date": run_date,
                "raw_record": psycopg.types.json.Jsonb(r),
            }
            cur.execute(
                """
                insert into cochise_leads (
                  source_county, document_id, recording_number, recording_date, document_type,
                  grantors, grantees, trustor, trustee, beneficiary, principal_amount, property_address,
                  detail_url, image_urls, ocr_method, ocr_chars, used_groq, groq_model, groq_error,
                  analysis_error, run_date, raw_record
                ) values (
                  %(source_county)s, %(document_id)s, %(recording_number)s, %(recording_date)s, %(document_type)s,
                  %(grantors)s, %(grantees)s, %(trustor)s, %(trustee)s, %(beneficiary)s, %(principal_amount)s, %(property_address)s,
                  %(detail_url)s, %(image_urls)s, %(ocr_method)s, %(ocr_chars)s, %(used_groq)s, %(groq_model)s, %(groq_error)s,
                  %(analysis_error)s, %(run_date)s, %(raw_record)s
                )
                on conflict (source_county, document_id) do update set
                  recording_number = excluded.recording_number,
                  recording_date   = excluded.recording_date,
                  document_type    = excluded.document_type,
                  grantors         = excluded.grantors,
                  grantees         = excluded.grantees,
                  trustor          = excluded.trustor,
                  trustee          = excluded.trustee,
                  beneficiary      = excluded.beneficiary,
                  principal_amount = excluded.principal_amount,
                  property_address = excluded.property_address,
                  detail_url       = excluded.detail_url,
                  image_urls       = excluded.image_urls,
                  ocr_method       = excluded.ocr_method,
                  ocr_chars        = excluded.ocr_chars,
                  used_groq        = excluded.used_groq,
                  groq_model       = excluded.groq_model,
                  groq_error       = excluded.groq_error,
                  analysis_error   = excluded.analysis_error,
                  run_date         = excluded.run_date,
                  raw_record       = excluded.raw_record,
                  updated_at       = now()
                returning (xmax = 0) as inserted;
                """,
                payload,
            )
            row = cur.fetchone()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated, llm_used


def _run_once(doc_types: list[str], workers: int, lookback_days: int, strict_llm: bool, ocr_limit: int) -> tuple[int, int, int, int]:
    today = date.today()
    lookback_days = max(1, int(lookback_days or 1))
    start_day = today - timedelta(days=lookback_days - 1)
    start_date = start_day.strftime("%-m/%-d/%Y")
    end_date = today.strftime("%-m/%-d/%Y")

    db_url = (os.environ.get("COCHISE_DB_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not os.environ.get("COCHISE_SKIP_DB"):
        if not db_url:
            raise RuntimeError("DATABASE_URL is missing (or pass --db-url)")
        with _connect_db(db_url) as conn:
            _ensure_schema(conn)

    res = run_cochise_pipeline(
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        max_pages=0,
        ocr_limit=ocr_limit,
        workers=max(1, workers),
        use_groq=True,
        headless=True,
        verbose=bool(os.environ.get("COCHISE_VERBOSE", "")),
        write_output_files=bool(os.environ.get("COCHISE_WRITE_FILES", "")),
    )

    records = res.get("records", [])
    if strict_llm:
        missing = [str(r.get("documentId", "") or "") for r in records if not bool(r.get("usedGroq", False))]
        if missing:
            sample = ", ".join(x for x in missing[:10] if x)
            raise RuntimeError(f"LLM coverage check failed before DB write: missing={len(missing)} sample=[{sample}]")
    if os.environ.get("COCHISE_SKIP_DB"):
        llm_used = sum(1 for r in records if bool(r.get("usedGroq", False)))
        return len(records), 0, 0, llm_used

    with _connect_db(db_url) as conn:
        inserted, updated, llm_used = _upsert_records(conn, records, today)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into cochise_pipeline_runs
                  (run_date, run_finished_at, total_records, inserted_rows, updated_rows, llm_used_rows, status)
                values (%s, now(), %s, %s, %s, %s, 'success');
                """,
                (today, len(records), inserted, updated, llm_used),
            )
        conn.commit()

    return len(records), inserted, updated, llm_used


def main() -> None:
    _load_env()
    llm_endpoint = (os.environ.get("GROQ_LLM_ENDPOINT_URL") or os.environ.get("GREENLEE_LLM_ENDPOINT_URL") or "").strip()
    llm_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not (llm_key or llm_endpoint):
        _log("warning: neither GROQ_API_KEY nor GROQ_LLM_ENDPOINT_URL is set; LLM extraction will be disabled")
    elif llm_endpoint and not llm_key:
        _log("info: using hosted LLM endpoint (GROQ_LLM_ENDPOINT_URL); GROQ_API_KEY not required")

    p = argparse.ArgumentParser(description="Run Cochise pipeline once and upsert into DB")
    p.add_argument("--lookback-days", type=int, default=14)
    p.add_argument("--workers", type=int, default=3)
    # Backwards compat: script used to loop forever unless --once.
    # We now always run once for faster, predictable execution.
    p.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--verbose", action="store_true", help="Print extractor progress while running")
    p.add_argument("--write-files", action="store_true", help="Write CSV/JSON artifacts under cochise/output")
    p.add_argument("--db-url", default="", help="Override DATABASE_URL (avoid editing .env)")
    p.add_argument("--skip-db", action="store_true", help="Skip DB upsert (still runs scraper + writes files)")
    p.add_argument("--strict-llm", action="store_true", help="Fail run if not all records used LLM")
    p.add_argument("--doc-types", nargs="+", default=UNIFIED_LEAD_DOC_TYPES)
    p.add_argument("--ocr-limit", type=int, default=0, help="Max records to OCR (0=all)")
    p.add_argument(
        "--no-sanitization",
        action="store_true",
        help="Disable LLM prompt sanitization/truncation rules (preserve extracted text as-is)",
    )
    args = p.parse_args()

    doc_types: list[str] = sorted({str(x).strip() for x in (args.doc_types or []) if str(x).strip()})

    # Plumb through to shared extractor without changing its signature.
    if args.verbose:
        os.environ["COCHISE_VERBOSE"] = "1"
    if args.write_files:
        os.environ["COCHISE_WRITE_FILES"] = "1"
    if str(args.db_url or "").strip():
        os.environ["COCHISE_DB_URL"] = str(args.db_url).strip()
    if args.skip_db:
        os.environ["COCHISE_SKIP_DB"] = "1"
    if args.no_sanitization:
        os.environ["COCHISE_NO_SANITIZATION"] = "1"

    _log(
        f"starting cochise runner "
        f"lookback_days={args.lookback_days} workers={args.workers}"
    )

    try:
        total, ins, upd, llm_used = _run_once(
            doc_types,
            args.workers,
            args.lookback_days,
            args.strict_llm,
            args.ocr_limit,
        )
        _log(f"run ok total={total} inserted={ins} updated={upd} llm_used={llm_used}")
    except Exception as exc:
        _log(f"run failed: {exc}")
        raise


if __name__ == "__main__":
    main()
