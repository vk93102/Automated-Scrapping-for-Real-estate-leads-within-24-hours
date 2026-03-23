from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# Allow running as: python greenlee/live_pipeline.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from greenlee.extractor import (  # noqa: E402
    DEFAULT_DOCUMENT_TYPES,
    run_greenlee_pipeline,
    sanitize_borrower_name,
    sanitize_property_address,
)


def _load_env() -> None:
    """Load environment variables from .env file."""
    root_dir = Path(__file__).resolve().parent.parent
    env_file = root_dir / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _db_url_with_ssl(url: str) -> str:
    """Add sslmode=require to database URL if not already present."""
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def _connect_db(database_url: str, retries: int = 3, sleep_s: int = 3) -> psycopg.Connection:
    """Connect to PostgreSQL database with retries."""
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
                print(f"Warning: DB host DNS resolution failed for {host}: {exc}")

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
    """Create greenlee_leads table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists greenlee_leads (
              id               bigserial primary key,
              source_county    text not null default 'Greenlee',
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
    conn.commit()


def _upsert_records_to_db(
    conn: psycopg.Connection, records: list[dict], run_date: date
) -> tuple[int, int, int]:
    """Upsert records to greenlee_leads table.
    
    Returns (inserted_count, updated_count, llm_used_count).
    """
    inserted = 0
    updated = 0
    llm_used = 0
    with conn.cursor() as cur:
        for r in records:
            doc_id = str(r.get("documentId", "") or "").strip()
            if not doc_id:
                continue
            clean_address = sanitize_property_address(r.get("propertyAddress", ""))
            clean_trustor = sanitize_borrower_name(r.get("trustor", ""))
            r_clean = dict(r)
            r_clean["propertyAddress"] = clean_address
            r_clean["trustor"] = clean_trustor
            used_groq = bool(r.get("usedGroq", False))
            if used_groq:
                llm_used += 1
            payload = {
                "source_county": r.get("sourceCounty") or "Greenlee",
                "document_id": doc_id,
                "recording_number": r.get("recordingNumber", ""),
                "recording_date": r.get("recordingDate", ""),
                "document_type": r.get("documentType", ""),
                "grantors": r.get("grantors", ""),
                "grantees": r.get("grantees", ""),
                "trustor": clean_trustor,
                "trustee": r.get("trustee", ""),
                "beneficiary": r.get("beneficiary", ""),
                "principal_amount": r.get("principalAmount", ""),
                "property_address": clean_address,
                "detail_url": r.get("detailUrl", ""),
                "image_urls": r.get("imageUrls", ""),
                "ocr_method": r.get("ocrMethod", ""),
                "ocr_chars": int(r.get("ocrChars") or 0),
                "used_groq": used_groq,
                "groq_model": r.get("groqModel", ""),
                "groq_error": r.get("groqError", ""),
                "analysis_error": r.get("analysisError", ""),
                "run_date": run_date,
                "raw_record": psycopg.types.json.Jsonb(r_clean),
            }
            cur.execute(
                """
                insert into greenlee_leads (
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
                                    recording_number = coalesce(nullif(excluded.recording_number, ''), greenlee_leads.recording_number),
                                    recording_date   = coalesce(nullif(excluded.recording_date, ''), greenlee_leads.recording_date),
                                    document_type    = coalesce(nullif(excluded.document_type, ''), greenlee_leads.document_type),
                                    grantors         = coalesce(nullif(excluded.grantors, ''), greenlee_leads.grantors),
                                    grantees         = coalesce(nullif(excluded.grantees, ''), greenlee_leads.grantees),
                                    trustor          = coalesce(nullif(excluded.trustor, ''), greenlee_leads.trustor),
                                    trustee          = coalesce(nullif(excluded.trustee, ''), greenlee_leads.trustee),
                                    beneficiary      = coalesce(nullif(excluded.beneficiary, ''), greenlee_leads.beneficiary),
                                    principal_amount = coalesce(nullif(excluded.principal_amount, ''), greenlee_leads.principal_amount),
                                    property_address = coalesce(nullif(excluded.property_address, ''), greenlee_leads.property_address),
                                    detail_url       = coalesce(nullif(excluded.detail_url, ''), greenlee_leads.detail_url),
                                    image_urls       = coalesce(nullif(excluded.image_urls, ''), greenlee_leads.image_urls),
                                    ocr_method       = coalesce(nullif(excluded.ocr_method, ''), greenlee_leads.ocr_method),
                                    ocr_chars        = greatest(coalesce(excluded.ocr_chars, 0), coalesce(greenlee_leads.ocr_chars, 0)),
                  used_groq        = excluded.used_groq,
                                    groq_model       = coalesce(nullif(excluded.groq_model, ''), greenlee_leads.groq_model),
                                    groq_error       = coalesce(nullif(excluded.groq_error, ''), greenlee_leads.groq_error),
                                    analysis_error   = coalesce(nullif(excluded.analysis_error, ''), greenlee_leads.analysis_error),
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


def _sanitize_existing_record_fields(conn: psycopg.Connection) -> int:
    """Normalize and cleanup property_address/trustor for already stored rows."""
    changed = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, trustor, property_address, raw_record
            from greenlee_leads
            where coalesce(trim(property_address), '') <> ''
               or coalesce(trim(trustor), '') <> ''
               or (raw_record ? 'propertyAddress')
               or (raw_record ? 'trustor');
            """
        )
        rows = cur.fetchall()

        for row_id, trustor, property_address, raw_record in rows:
            raw_obj = raw_record if isinstance(raw_record, dict) else {}
            raw_addr = str(raw_obj.get("propertyAddress", "") or "")
            raw_trustor = str(raw_obj.get("trustor", "") or "")
            clean_prop = sanitize_property_address(str(property_address or ""))
            clean_raw = sanitize_property_address(raw_addr)
            prev_prop = str(property_address or "")
            best_addr = clean_prop or clean_raw or prev_prop

            clean_trustor = sanitize_borrower_name(str(trustor or ""))
            clean_raw_trustor = sanitize_borrower_name(raw_trustor)
            prev_trustor = str(trustor or "")
            best_trustor = clean_trustor or clean_raw_trustor or prev_trustor

            next_raw = dict(raw_obj)
            prev_raw = raw_addr
            prev_raw_trustor = raw_trustor
            next_raw["propertyAddress"] = best_addr
            next_raw["trustor"] = best_trustor

            if (
                best_addr != prev_prop
                or best_addr != prev_raw
                or best_trustor != prev_trustor
                or best_trustor != prev_raw_trustor
            ):
                cur.execute(
                    """
                    update greenlee_leads
                    set trustor = %s,
                        property_address = %s,
                        raw_record = %s,
                        updated_at = now()
                    where id = %s;
                    """,
                    (best_trustor, best_addr, psycopg.types.json.Jsonb(next_raw), row_id),
                )
                changed += 1
    conn.commit()
    return changed


def _default_dates() -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=7)
    return start.strftime("%-m/%-d/%Y"), end.strftime("%-m/%-d/%Y")


def main() -> None:
    _load_env()
    dstart, dend = _default_dates()

    parser = argparse.ArgumentParser(
        description="Greenlee County, AZ — end-to-end leads pipeline"
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
    parser.add_argument("--store-db", action="store_true", help="Store results to PostgreSQL database")
    parser.add_argument("--write-files", action="store_true", help="Write output CSV/JSON files (default: disabled)")

    args = parser.parse_args()

    print("\n============================================================")
    print(" GREENLEE COUNTY AZ — REAL ESTATE LEADS PIPELINE")
    print("============================================================")
    print(f" Date Range : {args.start_date} -> {args.end_date}")
    print(f" Doc Types  : {len(args.doc_types)}")
    if args.store_db:
        print(f" Store DB   : enabled")

    res = run_greenlee_pipeline(
        start_date=args.start_date,
        end_date=args.end_date,
        doc_types=args.doc_types,
        max_pages=args.pages,
        ocr_limit=args.ocr_limit,
        workers=args.workers,
        use_groq=not args.no_groq,
        headless=not args.headful,
        verbose=args.verbose,
        write_output_files=args.write_files,
    )

    rows = res.get("records", [])
    with_addr = sum(1 for r in rows if r.get("propertyAddress"))
    with_amt = sum(1 for r in rows if r.get("principalAmount"))

    print("\n---------------- RESULT ----------------")
    print(f" Records      : {len(rows)}")
    print(f" With Address : {with_addr}")
    print(f" With Amount  : {with_amt}")
    if args.write_files:
        print(f" CSV          : {res.get('csv_path','')}")
        print(f" JSON         : {res.get('json_path','')}")

    # Store to database if requested
    db_inserted = 0
    db_updated = 0
    db_llm_used = 0
    if args.store_db:
        db_url = (os.environ.get("DATABASE_URL") or "").strip()
        if not db_url:
            print(" ❌ DB Store   : Failed (DATABASE_URL not set)")
        else:
            try:
                with _connect_db(db_url) as conn:
                    _ensure_schema(conn)
                    db_inserted, db_updated, db_llm_used = _upsert_records_to_db(conn, rows, date.today())
                    sanitized_existing = _sanitize_existing_record_fields(conn)
                if sanitized_existing:
                    print(f" ✓ DB Clean   : sanitized {sanitized_existing} existing borrower/address rows")
                print(f" ✓ DB Store   : {db_inserted} inserted, {db_updated} updated, {db_llm_used} used LLM")
            except Exception as e:
                print(f" ❌ DB Store   : Failed ({e})")

    print("----------------------------------------")


if __name__ == "__main__":
    main()
