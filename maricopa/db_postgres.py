from __future__ import annotations

from dataclasses import asdict
from typing import Optional, Any, Dict

import psycopg

from .extract_rules import ExtractedFields
from .maricopa_api import DocumentMetadata
import json


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        # Minimal schema: `documents` and `cron_jobs`.
        cur.execute(
            """
            create table if not exists documents (
              id               bigserial primary key,
              recording_number text unique not null,
              recording_date   text,
              document_type    text,
              page_amount      integer,
              names            text,
              restricted       boolean,
              metadata         jsonb,
              properties       jsonb,
              ocr_text         text,
              last_failure     jsonb,
              failed           boolean default false,
              last_processed_at timestamptz,
              created_at       timestamptz not null default now(),
              updated_at       timestamptz not null default now()
            );
            """
        )

        # Lightweight table to store cron/pipeline progress and logs. One row per run.
        cur.execute(
            """
            create table if not exists cron_jobs (
              id               bigserial primary key,
              run_id           text unique not null,
              job_name         text,
              status           text not null default 'running',
              total_found      integer,
              total_skipped    integer default 0,
              total_processed  integer default 0,
              total_failed     integer default 0,
              started_at       timestamptz not null default now(),
              finished_at      timestamptz,
              last_updated_at  timestamptz not null default now(),
              error_message    text,
              raw_log          text
            );
            """
        )

        # Explicit properties table with columns requested by user.
        cur.execute(
            """
            create table if not exists properties (
              id                         bigserial primary key,
              document_id                bigint references documents(id) on delete cascade,
              trustor_1_full_name        text,
              trustor_1_first_name       text,
              trustor_1_last_name        text,
              trustor_2_full_name        text,
              trustor_2_first_name       text,
              trustor_2_last_name        text,
              address_city               text,
              address_state              text,
              address_zip                text,
              property_address           text,
              sale_date                  text,
              original_principal_balance text,
              address_unit               text,
              llm_model                  text,
              created_at                 timestamptz not null default now(),
              updated_at                 timestamptz not null default now(),
              unique (document_id)
            );
            """
        )

        # Store discovered recording numbers (raw discovery) for auditing/backfill
        cur.execute(
            """
            create table if not exists discovered_recordings (
              id               bigserial primary key,
              recording_number text unique not null,
              metadata         jsonb,
              discovered_at    timestamptz not null default now()
            );
            """
        )

        # Backward/forward-compatible logical names for Maricopa-specific querying.
        cur.execute("create or replace view maricopa_documents as select * from documents;")
        cur.execute("create or replace view maricopa_properties as select * from properties;")
        cur.execute("create or replace view maricopa_cron_jobs as select * from cron_jobs;")
        cur.execute("create or replace view maricopa_discovered_recordings as select * from discovered_recordings;")
    conn.commit()


# ---------------------------------------------------------------------------
# Document upsert & OCR storage
# ---------------------------------------------------------------------------

def upsert_document(conn: psycopg.Connection, meta: DocumentMetadata) -> int:
    """Insert or update a document row from API metadata. Returns the row id."""
    doc_type = meta.document_codes[0] if meta.document_codes else None
    names_str = ", ".join(meta.names) if meta.names else None
    # Compact metadata JSON
    meta_json: Dict[str, Any] = {
        "recording_number": meta.recording_number,
        "recording_date": meta.recording_date,
        "document_codes": list(meta.document_codes) if getattr(meta, "document_codes", None) else None,
        "page_amount": meta.page_amount,
        "names": list(meta.names) if getattr(meta, "names", None) else None,
        "restricted": meta.restricted,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into documents
              (recording_number, recording_date, document_type, page_amount, names, restricted, metadata)
            values (%s, %s, %s, %s, %s, %s, %s::jsonb)
            on conflict (recording_number) do update set
              recording_date = excluded.recording_date,
              document_type  = excluded.document_type,
              page_amount    = excluded.page_amount,
              names          = excluded.names,
              restricted     = excluded.restricted,
              metadata       = excluded.metadata,
              updated_at     = now()
            returning id;
            """,
            (
                meta.recording_number,
                meta.recording_date,
                doc_type,
                meta.page_amount,
                names_str,
                meta.restricted,
                json.dumps(meta_json),
            ),
        )
        doc_id = int(cur.fetchone()[0])
    conn.commit()
    return doc_id


def insert_discovered_recording(conn: psycopg.Connection, recording_number: str, metadata: Optional[Dict[str, Any]] = None) -> int:
    """Insert or update a discovered recording number for auditing. Returns the row id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into discovered_recordings (recording_number, metadata)
            values (%s, %s::jsonb)
            on conflict (recording_number) do update set
              metadata = coalesce(discovered_recordings.metadata, excluded.metadata),
              discovered_at = coalesce(discovered_recordings.discovered_at, now())
            returning id;
            """,
            (recording_number, json.dumps(metadata) if metadata is not None else None),
        )
        row = cur.fetchone()
        rid = int(row[0]) if row else None
    conn.commit()
    return rid


def update_document_ocr_text(
    conn: psycopg.Connection,
    recording_number: str,
    ocr_text: str,
) -> None:
    """Store the full OCR text directly in the documents table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            update documents
            set ocr_text = %s, last_processed_at = now(), updated_at = now()
            where recording_number = %s;
            """,
            (ocr_text or "", recording_number),
        )
    conn.commit()



def mark_processed(conn: psycopg.Connection, recording_number: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update documents set last_processed_at = now(), updated_at = now() where recording_number = %s;",
            (recording_number,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Failure tracking
# ---------------------------------------------------------------------------

def record_failure(
    conn: psycopg.Connection,
    recording_number: str,
    *,
    stage: str,
    error: str,
) -> None:
    # Store last failure as JSON on the documents row (no separate failures table).
    failure_json = {
        "stage": stage,
        "error": (error or "")[:5000],
        "attempts": 1,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into documents (recording_number, failed, last_failure)
            values (%s, true, %s::jsonb)
            on conflict (recording_number) do update set
              failed = true,
              last_failure = %s::jsonb,
              updated_at = now();
            """,
            (recording_number, json.dumps(failure_json), json.dumps(failure_json)),
        )
    conn.commit()


def mark_resolved(conn: psycopg.Connection, recording_number: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update documents set failed = false, last_failure = null, updated_at = now() where recording_number = %s;",
            (recording_number,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def get_document_id(conn: psycopg.Connection, recording_number: str) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute("select id from documents where recording_number = %s;", (recording_number,))
        row = cur.fetchone()
        return int(row[0]) if row else None


def has_unresolved_failure(conn: psycopg.Connection, recording_number: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select failed from documents where recording_number = %s;",
            (recording_number,),
        )
        row = cur.fetchone()
        return False if not row else bool(row[0])


def has_any_failure(conn: psycopg.Connection, recording_number: str) -> bool:
    """Return True if this recording number has any row in scrape_failures,
    resolved or not. Used to skip permanently broken / 404 records."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from documents where recording_number = %s limit 1;",
            (recording_number,),
        )
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Extracted properties
# ---------------------------------------------------------------------------

def upsert_properties(
    conn: psycopg.Connection,
    document_id: int,
    fields: ExtractedFields,
    llm_model: Optional[str] = None,
) -> int:
    """Insert or update extracted properties row (idempotent — safe to call on retries)."""
    d = asdict(fields)
    # Map fields to the explicit properties table columns.
    vals = (
        document_id,
        d.get("trustor_1_full_name"),
        d.get("trustor_1_first_name"),
        d.get("trustor_1_last_name"),
        d.get("trustor_2_full_name"),
        d.get("trustor_2_first_name"),
        d.get("trustor_2_last_name"),
        d.get("address_city"),
        d.get("address_state"),
        d.get("address_zip"),
        d.get("property_address"),
        d.get("sale_date"),
        d.get("original_principal_balance"),
        d.get("address_unit"),
        llm_model,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into properties (
              document_id, trustor_1_full_name, trustor_1_first_name, trustor_1_last_name,
              trustor_2_full_name, trustor_2_first_name, trustor_2_last_name,
              address_city, address_state, address_zip, property_address,
              sale_date, original_principal_balance, address_unit, llm_model
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (document_id) do update set
              trustor_1_full_name = excluded.trustor_1_full_name,
              trustor_1_first_name = excluded.trustor_1_first_name,
              trustor_1_last_name = excluded.trustor_1_last_name,
              trustor_2_full_name = excluded.trustor_2_full_name,
              trustor_2_first_name = excluded.trustor_2_first_name,
              trustor_2_last_name = excluded.trustor_2_last_name,
              address_city = excluded.address_city,
              address_state = excluded.address_state,
              address_zip = excluded.address_zip,
              property_address = excluded.property_address,
              sale_date = excluded.sale_date,
              original_principal_balance = excluded.original_principal_balance,
              address_unit = excluded.address_unit,
              llm_model = excluded.llm_model,
              updated_at = now()
            returning id;
            """,
            vals,
        )
        row = cur.fetchone()
        prop_id = int(row[0]) if row else document_id
    conn.commit()
    return prop_id


# Keep old name as alias so any external callers don't break.
def insert_properties(
    conn: psycopg.Connection,
    document_id: int,
    fields: ExtractedFields,
) -> int:
    return upsert_properties(conn, document_id, fields)


# ---------------------------------------------------------------------------
# Pipeline run tracking
# ---------------------------------------------------------------------------

def start_pipeline_run(
    conn: psycopg.Connection,
    run_id: str,
    begin_date: str,
    end_date: str,
    total_found: int,
) -> int:
    """Record a new pipeline run, return the run row id."""
    # Record into cron_jobs table for unified, lightweight tracking.
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into cron_jobs (run_id, job_name, status, total_found, started_at)
            values (%s, %s, 'running', %s, now())
            on conflict (run_id) do update set
              total_found = excluded.total_found,
              status = 'running',
              last_updated_at = now(),
              started_at = coalesce(cron_jobs.started_at, now())
            returning id;
            """,
            (run_id, 'maricopa_pipeline', total_found),
        )
        run_db_id = int(cur.fetchone()[0])
    conn.commit()
    return run_db_id


def finish_pipeline_run(
    conn: psycopg.Connection,
    run_id: str,
    *,
    total_skipped: int = 0,
    total_processed: int = 0,
    total_failed: int = 0,
    total_ocr: int = 0,
    total_llm: int = 0,
    status: str = "success",
    error_message: Optional[str] = None,
) -> None:
    """Update the pipeline run row with final stats."""
    with conn.cursor() as cur:
        cur.execute(
            """
            update cron_jobs set
              status = %s,
              total_skipped = %s,
              total_processed = %s,
              total_failed = %s,
              finished_at = now(),
              last_updated_at = now(),
              error_message = %s
            where run_id = %s;
            """,
            (status, total_skipped, total_processed, total_failed, error_message, run_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url)
