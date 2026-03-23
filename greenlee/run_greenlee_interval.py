#!/usr/bin/env python3
"""Greenlee interval runner: fetch last N days and upsert records to DB."""

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

from greenlee.extractor import (  # noqa: E402
    run_greenlee_pipeline,
    sanitize_borrower_name,
    sanitize_property_address,
)


GREENLEE_TARGET_DOC_TYPES = [
    # Distressed sale / foreclosure signals
    "NOTICE",
    "LIS PENDENS",
    "FORECLOSURE",
    "LIEU OF FORECLOSURE",
    "TRUSTEE'S DEED",
    "SHERIFF'S DEED",
    "BANKRUPTCY",

    # Divorce-related filings
    "DIVORCE DECREE",
    "DISSOLUTION",
    "SEPARATION",

    # Probate / inheritance signals
    "PROBATE",
    "PERSONAL REPRESENTATIVE",
    "HEIRSHIP",

    # Tax-delinquency signals
    "TAX BILL",
    "TREASURER'S DEED",
    "TREASURER'S RETURN",
]


def _load_env() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _log(msg: str) -> None:
    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with (log_dir / "greenlee_interval.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
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
        cur.execute(
            """
            create table if not exists greenlee_pipeline_runs (
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


def _doc_type_matches_target(found_doc_type: str, target_doc_types: list[str]) -> bool:
    f = str(found_doc_type or "").strip().upper()
    if not f:
        return False
    for t in target_doc_types:
        tt = str(t or "").strip().upper()
        if not tt:
            continue
        if f == tt or tt in f or f in tt:
            return True
    return False


def _fetch_db_snapshot(conn: psycopg.Connection) -> tuple[int, tuple | None]:
    """Return total leads count and most recent pipeline run row."""
    with conn.cursor() as cur:
        cur.execute("select count(*) from greenlee_leads;")
        leads_total = int(cur.fetchone()[0])
        cur.execute(
            """
            select run_date, total_records, inserted_rows, updated_rows, llm_used_rows, status, run_finished_at
            from greenlee_pipeline_runs
            order by id desc
            limit 1;
            """
        )
        recent = cur.fetchone()
    return leads_total, recent


def _run_once(
    doc_types: list[str],
    workers: int,
    lookback_days: int,
    strict_llm: bool,
    ocr_limit: int,
    verbose: bool,
) -> tuple[int, int, int, int, int]:
    today = date.today()
    lookback_days = max(1, int(lookback_days or 1))
    start_day = today - timedelta(days=lookback_days - 1)
    start_date = start_day.strftime("%-m/%-d/%Y")
    end_date = today.strftime("%-m/%-d/%Y")

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is missing")
    with _connect_db(db_url) as conn:
        _ensure_schema(conn)

    # CRITICAL: ocr_limit controls extraction behavior:
    #  -1 = skip OCR/LLM entirely (WRONG for data population - use for speed when data already exists)
    #   0 = process ALL documents with OCR + Groq LLM (RECOMMENDED for backfill/new data)
    #   N = process first N docs with OCR + Groq LLM (for testing)
    # For proper data extraction, we MUST use ocr_limit=0
    effective_ocr_limit = ocr_limit
    if ocr_limit < 0:
        _log(f"warning: ocr_limit={ocr_limit} set to 0 for proper data extraction (trustor/trustee/address)")
        effective_ocr_limit = 0

    _log("collecting Greenlee records (Playwright + OCR stage) ... this can take several minutes")
    t0 = time.time()
    res = run_greenlee_pipeline(
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        max_pages=0,
        ocr_limit=effective_ocr_limit,
        workers=max(1, workers),
        use_groq=True,
        headless=True,
        verbose=verbose,
        write_output_files=False,
    )
    _log(f"collection stage finished in {time.time() - t0:.1f}s")

    records = res.get("records", [])
    # IMPORTANT: do not drop fetched records before DB upsert.
    # We keep all collected rows to avoid accidental skips.
    mismatched = [
        r for r in records
        if not _doc_type_matches_target(r.get("documentType", ""), doc_types)
    ]
    if mismatched:
        sample = [f"{str(x.get('documentId',''))}:{str(x.get('documentType',''))}" for x in mismatched[:10]]
        _log(
            f"note: {len(mismatched)} records have non-target document_type labels; "
            f"keeping all records (no pre-upsert skip). sample={sample}"
        )
    _log(f"processed {len(records)} documents; checking extraction quality...")
    
    # Validate data extraction
    records_with_trustor = len([r for r in records if (r.get("trustor") or "").strip()])
    records_with_groq = len([r for r in records if bool(r.get("usedGroq", False))])
    records_with_ocr = len([r for r in records if int(r.get("ocrChars", 0) or 0) > 0])
    records_with_addr = len([r for r in records if (r.get("propertyAddress") or "").strip()])
    
    _log(
        f"extraction quality: {records_with_ocr} with OCR text, {records_with_groq} used Groq LLM, "
        f"{records_with_trustor} have trustor, {records_with_addr} have address"
    )
    
    if strict_llm:
        missing = [str(r.get("documentId", "") or "") for r in records if not bool(r.get("usedGroq", False))]
        if missing:
            sample = ", ".join(x for x in missing[:10] if x)
            raise RuntimeError(f"LLM coverage check failed before DB write: missing={len(missing)} sample=[{sample}]")
    with _connect_db(db_url) as conn:
        inserted, updated, llm_used = _upsert_records(conn, records, today)
        sanitized_existing = _sanitize_existing_record_fields(conn)
        if sanitized_existing:
            _log(f"sanitized borrower/address on {sanitized_existing} existing rows")
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into greenlee_pipeline_runs
                  (run_date, run_finished_at, total_records, inserted_rows, updated_rows, llm_used_rows, status)
                values (%s, now(), %s, %s, %s, %s, 'success');
                """,
                (today, len(records), inserted, updated, llm_used),
            )
        conn.commit()
        leads_total, _ = _fetch_db_snapshot(conn)

    return len(records), inserted, updated, llm_used, leads_total


def main() -> None:
    _load_env()
    if not (os.environ.get("GROQ_API_KEY") or "").strip():
        _log("warning: GROQ_API_KEY missing; LLM extraction will be disabled")

    p = argparse.ArgumentParser(description="Run Greenlee pipeline once and upsert into DB")
    p.add_argument("--interval-minutes", type=float, default=0.0, help="Deprecated: ignored (runner always executes once)")
    p.add_argument("--lookback-days", type=int, default=7)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--ocr-limit", type=int, default=0, help="0 means OCR+LLM for all records, -1 skip OCR")
    p.add_argument("--verbose", action="store_true", help="Print extractor progress while running")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one cycle and exit (default)")
    mode.add_argument("--loop", action="store_true", help="Deprecated: ignored (runner always executes once)")
    p.add_argument("--strict-llm", action="store_true", help="Fail run if not all records used LLM")
    p.add_argument("--doc-types", nargs="+", default=GREENLEE_TARGET_DOC_TYPES)
    args = p.parse_args()

    run_once = True
    if args.loop:
        _log("warning: --loop requested but ignored; runner is forced to single-run mode")
    if args.interval_minutes:
        _log("warning: --interval-minutes is deprecated and ignored; runner is forced to single-run mode")
    _log(
        f"starting greenlee single-run runner "
        f"lookback_days={args.lookback_days} once={run_once} workers={args.workers} "
        f"ocr_limit={args.ocr_limit} doc_types={len(args.doc_types)} verbose={args.verbose}"
    )
    try:
        total, ins, upd, llm_used, leads_total = _run_once(
            args.doc_types,
            args.workers,
            args.lookback_days,
            args.strict_llm,
            args.ocr_limit,
            args.verbose,
        )
        _log(f"run ok total={total} inserted={ins} updated={upd} llm_used={llm_used} db_total={leads_total}")
    except Exception as exc:
        _log(f"run failed: {exc}")
        raise


if __name__ == "__main__":
    main()
