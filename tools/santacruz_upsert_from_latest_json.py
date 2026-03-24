from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import psycopg


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


def _connect_db(database_url: str, retries: int = 3, sleep_s: int = 2) -> psycopg.Connection:
    primary_url = _db_url_with_ssl(database_url)

    host = (urlparse(primary_url).hostname or "").strip()
    if host:
        socket.getaddrinfo(host, 5432)

    last_err: Exception | None = None
    for i in range(max(1, retries)):
        try:
            return psycopg.connect(primary_url, connect_timeout=12)
        except Exception as exc:
            last_err = exc
            if i < retries - 1:
                time.sleep(sleep_s)

    raise RuntimeError(f"DB connect failed after {retries} attempts: {last_err}")


def _ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists santacruz_leads (
              id               bigserial primary key,
              source_county    text not null default 'Santa Cruz',
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
              document_urls    text,
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
        cur.execute("alter table santacruz_leads add column if not exists document_urls text;")
        cur.execute("alter table santacruz_leads add column if not exists manual_review boolean;")
        cur.execute("alter table santacruz_leads add column if not exists manual_review_reasons text;")
        cur.execute("alter table santacruz_leads add column if not exists manual_review_summary text;")
        cur.execute("alter table santacruz_leads add column if not exists manual_review_context text;")
    conn.commit()


def _latest_json_path() -> Path | None:
    paths = sorted(glob.glob("SANTA CRUZ/output/santacruz_leads_*.json"))
    if not paths:
        return None
    return Path(paths[-1])


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

            address = str(r.get("propertyAddress", "") or "")
            if address.upper().strip().startswith("PARCEL ID"):
                address = "NOT_FOUND"

            payload = {
                "source_county": r.get("sourceCounty") or "Santa Cruz",
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
                "property_address": address,
                "detail_url": r.get("detailUrl", ""),
                "image_urls": r.get("imageUrls", ""),
                "document_urls": r.get("documentUrls", ""),
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
                insert into santacruz_leads (
                  source_county, document_id, recording_number, recording_date, document_type,
                  grantors, grantees, trustor, trustee, beneficiary, principal_amount, property_address,
                                    detail_url, image_urls, document_urls, manual_review, manual_review_reasons, manual_review_summary, manual_review_context,
                                    ocr_method, ocr_chars, used_groq, groq_model, groq_error,
                  analysis_error, run_date, raw_record
                ) values (
                  %(source_county)s, %(document_id)s, %(recording_number)s, %(recording_date)s, %(document_type)s,
                  %(grantors)s, %(grantees)s, %(trustor)s, %(trustee)s, %(beneficiary)s, %(principal_amount)s, %(property_address)s,
                                    %(detail_url)s, %(image_urls)s, %(document_urls)s, %(manual_review)s, %(manual_review_reasons)s, %(manual_review_summary)s, %(manual_review_context)s,
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
                  document_urls    = excluded.document_urls,
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


def main() -> int:
    p = argparse.ArgumentParser(description="Upsert Santa Cruz leads into DB from a previously exported JSON artifact")
    p.add_argument("--db-url-file", default=".supabase_database_url", help="File containing DATABASE_URL")
    p.add_argument("--json", default="", help="Path to santacruz_leads_*.json (defaults to latest)")
    args = p.parse_args()

    db_url_file = Path(args.db_url_file)
    if not db_url_file.exists():
        raise SystemExit(f"Missing db url file: {db_url_file}")

    db_url = db_url_file.read_text(encoding="utf-8", errors="ignore").strip()
    if not db_url:
        raise SystemExit("DB url file is empty")

    json_path = Path(args.json).expanduser() if args.json else (_latest_json_path() or Path(""))
    if not json_path or not json_path.exists():
        raise SystemExit("No Santa Cruz JSON artifact found (expected SANTA CRUZ/output/santacruz_leads_*.json)")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    if not isinstance(records, list):
        raise SystemExit("Invalid JSON artifact: records is not a list")

    print(f"json={json_path}")
    print(f"records={len(records)}")

    with _connect_db(db_url) as conn:
        _ensure_schema(conn)
        inserted, updated, llm_used = _upsert_records(conn, records, run_date=date.today())
        with conn.cursor() as cur:
            cur.execute("select count(*) from santacruz_leads;")
            total = int(cur.fetchone()[0])

    print(f"inserted={inserted} updated={updated} llm_used={llm_used} total_in_db={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
