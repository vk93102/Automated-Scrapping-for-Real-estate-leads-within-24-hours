from __future__ import annotations

from dataclasses import asdict
from typing import Optional

import psycopg

from .extract_rules import ExtractedFields
from .maricopa_api import DocumentMetadata


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        # Core documents table — every recording number lands here immediately after metadata fetch.
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
              ocr_text         text,
              ocr_text_path    text,
              last_processed_at timestamptz,
              created_at       timestamptz not null default now()
            );
            """
        )

        # Backward-compatible column additions so existing tables upgrade in place.
        for col_sql in [
            "alter table documents add column if not exists names text;",
            "alter table documents add column if not exists restricted boolean;",
            "alter table documents add column if not exists ocr_text text;",
            "alter table documents add column if not exists ocr_text_path text;",
            "alter table documents add column if not exists last_processed_at timestamptz;",
        ]:
            cur.execute(col_sql)

        # Extracted lead fields from OCR.
        cur.execute(
            """
            create table if not exists properties (
              id               bigserial primary key,
              document_id      bigint unique references documents(id) on delete cascade,
              trustor_1_full_name  text,
              trustor_1_first_name text,
              trustor_1_last_name  text,
              trustor_2_full_name  text,
              trustor_2_first_name text,
              trustor_2_last_name  text,
              property_address text,
              address_city     text,
              address_state    text,
              address_zip      text,
              address_unit     text,
              sale_date        text,
              original_principal_balance text,
              llm_model        text,
              created_at       timestamptz not null default now(),
              updated_at       timestamptz not null default now()
            );
            """
        )

        # Ensure unique constraint on document_id — deduplicate first to avoid constraint errors.
        cur.execute(
            """
            do $$ begin
              if not exists (
                select 1 from pg_constraint where conname = 'properties_document_id_unique'
              ) then
                -- Remove duplicates (keep newest row per document)
                delete from properties a using properties b
                  where a.id < b.id and a.document_id = b.document_id;
                alter table properties add constraint properties_document_id_unique unique (document_id);
              end if;
            end $$;
            """
        )

        # Backward-compatible column additions for properties.
        for col_sql in [
            "alter table properties add column if not exists llm_model text;",
            "alter table properties add column if not exists updated_at timestamptz not null default now();",
        ]:
            cur.execute(col_sql)

        # Per-recording failure tracking for retries.
        cur.execute(
            """
            create table if not exists scrape_failures (
              id               bigserial primary key,
              recording_number text unique not null,
              stage            text not null,
              error            text,
              attempts         integer not null default 1,
              resolved         boolean not null default false,
              last_attempt_at  timestamptz not null default now()
            );
            """
        )

        # Pipeline run audit table — one row per cron execution.
        cur.execute(
            """
            create table if not exists pipeline_runs (
              id               bigserial primary key,
              run_id           text unique not null,
              begin_date       text,
              end_date         text,
              status           text not null default 'running',
              total_found      integer,
              total_skipped    integer default 0,
              total_processed  integer default 0,
              total_failed     integer default 0,
              total_ocr        integer default 0,
              total_llm        integer default 0,
              started_at       timestamptz not null default now(),
              finished_at      timestamptz,
              error_message    text
            );
            """
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Document upsert & OCR storage
# ---------------------------------------------------------------------------

def upsert_document(conn: psycopg.Connection, meta: DocumentMetadata) -> int:
    """Insert or update a document row from API metadata. Returns the row id."""
    doc_type = meta.document_codes[0] if meta.document_codes else None
    names_str = ", ".join(meta.names) if meta.names else None
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into documents
              (recording_number, recording_date, document_type, page_amount, names, restricted)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (recording_number) do update set
              recording_date = excluded.recording_date,
              document_type  = excluded.document_type,
              page_amount    = excluded.page_amount,
              names          = excluded.names,
              restricted     = excluded.restricted
            returning id;
            """,
            (
                meta.recording_number,
                meta.recording_date,
                doc_type,
                meta.page_amount,
                names_str,
                meta.restricted,
            ),
        )
        doc_id = int(cur.fetchone()[0])
    conn.commit()
    return doc_id


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
            set ocr_text = %s, last_processed_at = now()
            where recording_number = %s;
            """,
            (ocr_text or "", recording_number),
        )
    conn.commit()



def mark_processed(conn: psycopg.Connection, recording_number: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update documents set last_processed_at = now() where recording_number = %s;",
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
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into scrape_failures (recording_number, stage, error, attempts, resolved)
            values (%s, %s, %s, 1, false)
            on conflict (recording_number) do update set
              stage           = excluded.stage,
              error           = excluded.error,
              attempts        = scrape_failures.attempts + 1,
              resolved        = false,
              last_attempt_at = now();
            """,
            (recording_number, stage, (error or "")[:5000]),
        )
    conn.commit()


def mark_resolved(conn: psycopg.Connection, recording_number: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update scrape_failures set resolved = true, last_attempt_at = now() where recording_number = %s;",
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
            "select resolved from scrape_failures where recording_number = %s;",
            (recording_number,),
        )
        row = cur.fetchone()
        return False if not row else (bool(row[0]) is False)


def has_any_failure(conn: psycopg.Connection, recording_number: str) -> bool:
    """Return True if this recording number has any row in scrape_failures,
    resolved or not. Used to skip permanently broken / 404 records."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from scrape_failures where recording_number = %s limit 1;",
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
    cols = [
        "document_id",
        "trustor_1_full_name",
        "trustor_1_first_name",
        "trustor_1_last_name",
        "trustor_2_full_name",
        "trustor_2_first_name",
        "trustor_2_last_name",
        "property_address",
        "address_city",
        "address_state",
        "address_zip",
        "address_unit",
        "sale_date",
        "original_principal_balance",
        "llm_model",
    ]
    vals = [document_id] + [d.get(c) for c in cols[1:-1]] + [llm_model]
    update_set = ", ".join(
        f"{c} = excluded.{c}"
        for c in cols
        if c != "document_id"
    ) + ", updated_at = now()"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            insert into properties ({', '.join(cols)})
            values ({', '.join(['%s'] * len(cols))})
            on conflict (document_id) do update set {update_set}
            returning id;
            """,
            vals,
        )
        prop_id = int(cur.fetchone()[0])
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
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into pipeline_runs (run_id, begin_date, end_date, status, total_found)
            values (%s, %s, %s, 'running', %s)
            on conflict (run_id) do update set
              total_found = excluded.total_found,
              started_at  = now(),
              status      = 'running'
            returning id;
            """,
            (run_id, begin_date, end_date, total_found),
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
            update pipeline_runs set
              status          = %s,
              total_skipped   = %s,
              total_processed = %s,
              total_failed    = %s,
              total_ocr       = %s,
              total_llm       = %s,
              finished_at     = now(),
              error_message   = %s
            where run_id = %s;
            """,
            (status, total_skipped, total_processed, total_failed,
             total_ocr, total_llm, error_message, run_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url)
