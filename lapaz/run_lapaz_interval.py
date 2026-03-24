#!/usr/bin/env python3
"""La Paz runner: fetch recent leads and upsert unique rows to DB."""

from __future__ import annotations

import argparse
import hashlib
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

from lapaz.extractor import DEFAULT_DOCUMENT_TYPES, run_lapaz_pipeline  # noqa: E402


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
    with (log_dir / "lapaz_interval.log").open("a", encoding="utf-8") as f:
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
            create table if not exists lapaz_leads (
              id               bigserial primary key,
              source_county    text not null default 'La Paz',
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
                            manual_review    boolean,
                            manual_review_reasons text,
                            manual_review_summary text,
                            manual_review_context text,
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
        # Backward-compatible schema migrations for existing installations.
        cur.execute("alter table lapaz_leads add column if not exists source_county text not null default 'La Paz';")
        cur.execute("alter table lapaz_leads add column if not exists recording_number text;")
        cur.execute("alter table lapaz_leads add column if not exists recording_date text;")
        cur.execute("alter table lapaz_leads add column if not exists document_type text;")
        cur.execute("alter table lapaz_leads add column if not exists grantors text;")
        cur.execute("alter table lapaz_leads add column if not exists grantees text;")
        cur.execute("alter table lapaz_leads add column if not exists trustor text;")
        cur.execute("alter table lapaz_leads add column if not exists trustee text;")
        cur.execute("alter table lapaz_leads add column if not exists beneficiary text;")
        cur.execute("alter table lapaz_leads add column if not exists principal_amount text;")
        cur.execute("alter table lapaz_leads add column if not exists property_address text;")
        cur.execute("alter table lapaz_leads add column if not exists detail_url text;")
        cur.execute("alter table lapaz_leads add column if not exists image_urls text;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review boolean;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review_reasons text;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review_summary text;")
        cur.execute("alter table lapaz_leads add column if not exists manual_review_context text;")
        cur.execute("alter table lapaz_leads add column if not exists ocr_method text;")
        cur.execute("alter table lapaz_leads add column if not exists ocr_chars integer;")
        cur.execute("alter table lapaz_leads add column if not exists used_groq boolean;")
        cur.execute("alter table lapaz_leads add column if not exists groq_model text;")
        cur.execute("alter table lapaz_leads add column if not exists groq_error text;")
        cur.execute("alter table lapaz_leads add column if not exists analysis_error text;")
        cur.execute("alter table lapaz_leads add column if not exists run_date date;")
        cur.execute("alter table lapaz_leads add column if not exists raw_record jsonb not null default '{}'::jsonb;")
        cur.execute("alter table lapaz_leads add column if not exists created_at timestamptz not null default now();")
        cur.execute("alter table lapaz_leads add column if not exists updated_at timestamptz not null default now();")
        cur.execute(
            """
            create unique index if not exists lapaz_leads_source_document_uidx
            on lapaz_leads (source_county, document_id);
            """
        )
        cur.execute(
            """
            create table if not exists lapaz_pipeline_runs (
              id              bigserial primary key,
              run_started_at  timestamptz not null default now(),
              run_finished_at timestamptz,
              run_date        date,
              total_records   integer default 0,
                            records_missing_document_id integer default 0,
                            records_with_ocr integer default 0,
                            records_used_groq integer default 0,
                            records_with_trustor integer default 0,
                            records_with_groq_error integer default 0,
                            manual_review_true integer default 0,
              inserted_rows   integer default 0,
              updated_rows    integer default 0,
              llm_used_rows   integer default 0,
                            lookback_days   integer,
                            workers         integer,
                            ocr_limit       integer,
                            strict_llm      boolean,
                            sanitization_disabled boolean,
                            strict_valuation_disabled boolean,
              status          text not null default 'running',
              error_message   text,
              created_at      timestamptz not null default now()
            );
            """
        )
        cur.execute("alter table lapaz_pipeline_runs add column if not exists llm_used_rows integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists run_started_at timestamptz not null default now();")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists run_finished_at timestamptz;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists run_date date;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists total_records integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists records_missing_document_id integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists records_with_ocr integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists records_used_groq integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists records_with_trustor integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists records_with_groq_error integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists manual_review_true integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists inserted_rows integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists updated_rows integer default 0;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists status text not null default 'running';")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists error_message text;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists lookback_days integer;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists workers integer;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists ocr_limit integer;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists strict_llm boolean;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists sanitization_disabled boolean;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists strict_valuation_disabled boolean;")
        cur.execute("alter table lapaz_pipeline_runs add column if not exists created_at timestamptz not null default now();")
    conn.commit()


def _fetch_db_snapshot(database_url: str) -> dict:
    try:
        with _connect_db(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) from lapaz_leads;")
                row = cur.fetchone()
                leads_total = int(row[0]) if row and row[0] is not None else 0
        return {"lapaz_leads_total": leads_total}
    except Exception as exc:
        _log(f"warning: could not fetch DB snapshot: {exc}")
        return {}


def _record_failed_run(
    *,
    db_url: str,
    run_date: date,
    lookback_days: int,
    workers: int,
    ocr_limit: int,
    strict_llm: bool,
    sanitization_disabled: bool,
    strict_valuation_disabled: bool,
    error_message: str,
) -> None:
    if not db_url:
        return
    try:
        with _connect_db(db_url) as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into lapaz_pipeline_runs (
                      run_date,
                      run_finished_at,
                      total_records,
                      records_missing_document_id,
                      records_with_ocr,
                      records_used_groq,
                      records_with_trustor,
                      records_with_groq_error,
                      manual_review_true,
                      inserted_rows,
                      updated_rows,
                      llm_used_rows,
                      lookback_days,
                      workers,
                      ocr_limit,
                      strict_llm,
                      sanitization_disabled,
                      strict_valuation_disabled,
                      status,
                      error_message
                    ) values (
                      %s, now(), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, %s, %s, %s, %s, %s, %s, 'failed', %s
                    );
                    """,
                    (
                        run_date,
                        int(lookback_days or 0),
                        int(workers or 0),
                        int(ocr_limit or 0),
                        bool(strict_llm),
                        bool(sanitization_disabled),
                        bool(strict_valuation_disabled),
                        str(error_message or "")[:2000],
                    ),
                )
            conn.commit()
    except Exception as exc:
        _log(f"warning: failed to record failed run into DB: {exc}")


def _upsert_records(conn: psycopg.Connection, records: list[dict], run_date: date) -> tuple[int, int, int]:
    inserted = 0
    updated = 0
    llm_used = 0
    with conn.cursor() as cur:
        for r in records:
            doc_id = str(r.get("documentId", "") or "").strip()
            if not doc_id:
                # Avoid silently skipping: generate a stable synthetic ID from available fields.
                basis = "|".join(
                    [
                        str(r.get("detailUrl", "") or "").strip(),
                        str(r.get("recordingNumber", "") or "").strip(),
                        str(r.get("recordingDate", "") or "").strip(),
                        str(r.get("documentType", "") or "").strip(),
                    ]
                )
                digest = hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:16]
                doc_id = f"synthetic:{digest}"
                r["documentId"] = doc_id
                r["syntheticDocumentId"] = True
            used_groq = bool(r.get("usedGroq", False))
            if used_groq:
                llm_used += 1
            property_address = str(r.get("propertyAddress") or "").strip()
            payload = {
                "source_county": r.get("sourceCounty") or "La Paz",
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
                "property_address": property_address,
                "detail_url": r.get("detailUrl", ""),
                "image_urls": r.get("imageUrls", ""),
                "manual_review": bool(r.get("manualReview", False)),
                "manual_review_reasons": r.get("manualReviewReasons", ""),
                "manual_review_summary": r.get("manualReviewSummary", ""),
                "manual_review_context": r.get("manualReviewContext", ""),
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
                insert into lapaz_leads (
                  source_county, document_id, recording_number, recording_date, document_type,
                  grantors, grantees, trustor, trustee, beneficiary, principal_amount, property_address,
                                    detail_url, image_urls, manual_review, manual_review_reasons, manual_review_summary, manual_review_context,
                                    ocr_method, ocr_chars, used_groq, groq_model, groq_error,
                  analysis_error, run_date, raw_record
                ) values (
                  %(source_county)s, %(document_id)s, %(recording_number)s, %(recording_date)s, %(document_type)s,
                  %(grantors)s, %(grantees)s, %(trustor)s, %(trustee)s, %(beneficiary)s, %(principal_amount)s, %(property_address)s,
                                    %(detail_url)s, %(image_urls)s, %(manual_review)s, %(manual_review_reasons)s, %(manual_review_summary)s, %(manual_review_context)s,
                                    %(ocr_method)s, %(ocr_chars)s, %(used_groq)s, %(groq_model)s, %(groq_error)s,
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
                  manual_review          = excluded.manual_review,
                  manual_review_reasons  = excluded.manual_review_reasons,
                  manual_review_summary  = excluded.manual_review_summary,
                  manual_review_context  = excluded.manual_review_context,
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


def _run_once(
    doc_types: list[str],
    workers: int,
    lookback_days: int,
    ocr_limit: int,
    strict_llm: bool,
    verbose: bool,
    realtime: bool,
) -> tuple[int, int, int, int, int]:
    today = date.today()
    lookback_days = max(1, int(lookback_days or 1))
    start_day = today - timedelta(days=lookback_days - 1)
    start_date = start_day.strftime("%-m/%-d/%Y")
    end_date = today.strftime("%-m/%-d/%Y")
    res = run_lapaz_pipeline(
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        max_pages=0,
        ocr_limit=ocr_limit,
        workers=max(1, workers),
        use_groq=True,
        headless=True,
        verbose=verbose,
        write_output_files=False,
    )

    records = res.get("records", [])
    _log(f"processed {len(records)} documents; checking extraction quality...")

    records_missing_document_id = len([r for r in records if not str(r.get("documentId", "") or "").strip()])

    records_with_trustor = len([r for r in records if (r.get("trustor") or "").strip()])
    records_with_groq = len([r for r in records if bool(r.get("usedGroq", False))])
    records_with_ocr = len([r for r in records if int(r.get("ocrChars", 0) or 0) > 0])
    records_with_groq_error = len([r for r in records if (r.get("groqError") or "").strip()])
    records_manual_review = len([r for r in records if bool(r.get("manualReview", False))])

    sanitization_disabled = str(os.environ.get("LA_PAZ_DISABLE_SANITIZATION", "0")).strip() == "1"
    strict_valuation_disabled = str(os.environ.get("LA_PAZ_DISABLE_STRICT_VALUATION", "0")).strip() == "1"
    llm_regex_fallback_enabled = str(os.environ.get("LA_PAZ_LLM_REGEX_FALLBACK", "0")).strip() == "1"
    sample_groq_error = ""
    for r in records:
        err = (r.get("groqError") or "").strip()
        if err:
            sample_groq_error = err
            break
    _log(
        f"extraction quality: {records_with_ocr} with OCR text, "
        f"{records_with_groq} used Groq LLM, {records_with_trustor} have trustor, "
        f"llm_regex_fallback={llm_regex_fallback_enabled}"
    )
    if records_missing_document_id:
        _log(f"warning: {records_missing_document_id} records missing documentId (will use synthetic ids for DB upsert)")
    if records_with_groq_error:
        _log(
            f"llm diagnostics: {records_with_groq_error} Groq call failures; "
            f"sample_error={sample_groq_error[:220]}"
        )

    if strict_llm:
        missing = [str(r.get("documentId", "") or "") for r in records if not bool(r.get("usedGroq", False))]
        if missing:
            sample = ", ".join(x for x in missing[:10] if x)
            raise RuntimeError(f"LLM coverage check failed before DB write: missing={len(missing)} sample=[{sample}]")

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is missing")

    with _connect_db(db_url) as conn:
        _ensure_schema(conn)
        if realtime:
            _log("realtime: upserting records (docId, type, address)...")
            for i, r in enumerate(records[:50]):
                doc_id = str(r.get("documentId", "") or "").strip()
                addr = str(r.get("propertyAddress") or "").strip()
                addr_kind = "street"
                upper = addr.upper()
                if not addr:
                    addr_kind = "empty"
                elif upper.startswith("PARCEL") or upper.startswith("APN") or "PARCEL ID" in upper:
                    addr_kind = "parcel"
                _log(
                    f"realtime[{i+1}/{min(len(records),50)}] docId={doc_id or '(missing)'} "
                    f"type={str(r.get('documentType','') or '').strip()} addr_kind={addr_kind} addr={addr[:160]}"
                )

        inserted, updated, llm_used = _upsert_records(conn, records, today)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into lapaz_pipeline_runs
                                    (
                                        run_date,
                                        run_finished_at,
                                        total_records,
                                        records_missing_document_id,
                                        records_with_ocr,
                                        records_used_groq,
                                        records_with_trustor,
                                        records_with_groq_error,
                                        manual_review_true,
                                        inserted_rows,
                                        updated_rows,
                                        llm_used_rows,
                                        lookback_days,
                                        workers,
                                        ocr_limit,
                                        strict_llm,
                                        sanitization_disabled,
                                        strict_valuation_disabled,
                                        status
                                    )
                                values (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'success');
                """,
                (
                    today,
                    len(records),
                    records_missing_document_id,
                    records_with_ocr,
                    records_with_groq,
                    records_with_trustor,
                    records_with_groq_error,
                    records_manual_review,
                    inserted,
                    updated,
                    llm_used,
                    lookback_days,
                    max(1, workers),
                    ocr_limit,
                    bool(strict_llm),
                    bool(sanitization_disabled),
                    bool(strict_valuation_disabled),
                ),
            )
        conn.commit()

    db_snapshot = _fetch_db_snapshot(db_url)
    leads_total = db_snapshot.get("lapaz_leads_total", 0)
    return len(records), inserted, updated, llm_used, leads_total


def main() -> None:
    _load_env()

    # La Paz default behavior: run lenient (no aggressive sanitization or strict valuation).
    # Users can override by setting these env vars to 0.
    os.environ.setdefault("LA_PAZ_DISABLE_SANITIZATION", "1")
    os.environ.setdefault("LA_PAZ_DISABLE_STRICT_VALUATION", "1")
    os.environ.setdefault("LA_PAZ_LLM_REGEX_FALLBACK", "1")
    llm_endpoint = (os.environ.get("GROQ_LLM_ENDPOINT_URL") or os.environ.get("GREENLEE_LLM_ENDPOINT_URL") or "").strip()
    llm_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not (llm_key or llm_endpoint):
        _log("warning: neither GROQ_API_KEY nor GROQ_LLM_ENDPOINT_URL is set; LLM extraction will be disabled")
    elif llm_endpoint and not llm_key:
        _log("info: using hosted LLM endpoint (GROQ_LLM_ENDPOINT_URL); GROQ_API_KEY not required")

    p = argparse.ArgumentParser(description="Run La Paz pipeline on interval and upsert into DB")
    p.add_argument("--lookback-days", type=int, default=14)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--ocr-limit", type=int, default=0, help="0 process all docs with OCR+LLM (recommended), N cap, -1 skip OCR/LLM")
    p.add_argument("--verbose", action="store_true", help="Print extractor progress while running")
    p.add_argument("--realtime", action="store_true", help="Print per-record summary before DB upsert")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one cycle and exit")
    mode.add_argument("--loop", action="store_true", help="Run continuously on interval")
    p.add_argument("--strict-llm", action="store_true", help="Fail run if not all records used LLM")
    p.add_argument("--doc-types", nargs="+", default=DEFAULT_DOCUMENT_TYPES)
    args = p.parse_args()

    run_once = not args.loop
    _log(
        f"starting lapaz interval runner "
        f"lookback_days={args.lookback_days} once={run_once} workers={args.workers} "
        f"ocr_limit={args.ocr_limit} doc_types={len(args.doc_types)} verbose={args.verbose}"
    )

    while True:
        started = datetime.now()
        try:
            total, ins, upd, llm_used, db_total = _run_once(
                args.doc_types,
                args.workers,
                args.lookback_days,
                args.ocr_limit,
                args.strict_llm,
                args.verbose,
                args.realtime,
            )
            _log(f"run ok total={total} inserted={ins} updated={upd} llm_used={llm_used} db_total={db_total}")
        except Exception as exc:
            _log(f"run failed: {exc}")

            db_url = (os.environ.get("DATABASE_URL") or "").strip()
            sanitization_disabled = str(os.environ.get("LA_PAZ_DISABLE_SANITIZATION", "0")).strip() == "1"
            strict_valuation_disabled = str(os.environ.get("LA_PAZ_DISABLE_STRICT_VALUATION", "0")).strip() == "1"
            _record_failed_run(
                db_url=db_url,
                run_date=date.today(),
                lookback_days=args.lookback_days,
                workers=args.workers,
                ocr_limit=args.ocr_limit,
                strict_llm=args.strict_llm,
                sanitization_disabled=sanitization_disabled,
                strict_valuation_disabled=strict_valuation_disabled,
                error_message=str(exc),
            )

        if run_once:
            break

        _log(f"sleeping before next run")
        time.sleep(60)


if __name__ == "__main__":
    main()
