
"""
Database interaction for Coconino County module.
Handles connection to Supabase (PostgreSQL) and record upsertion.
"""

from __future__ import annotations

import os
import time
import hashlib
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse
import socket

import psycopg
from psycopg.types.json import Jsonb


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [DB] {msg}")


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def connect_db(database_url: str | None = None, retries: int = 3, sleep_s: int = 3) -> psycopg.Connection:
    """Connect to the database with retries."""
    if not database_url:
        database_url = os.environ.get("DATABASE_URL")
    
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    primary_url = _db_url_with_ssl(database_url)
    fallback_raw = (os.environ.get("DATABASE_URL_POOLER") or "").strip()
    fallback_url = _db_url_with_ssl(fallback_raw) if fallback_raw else ""

    host = (urlparse(primary_url).hostname or "").strip()
    if host:
        try:
            socket.getaddrinfo(host, 5432)
        except Exception as exc:
            _log(f"Primary DB host DNS resolution failed for {host}: {exc}")
            if not fallback_url:
                raise

    candidates = [u for u in [primary_url, fallback_url] if u]
    last_err: Exception | None = None

    for url in candidates:
        for i in range(max(1, retries)):
            try:
                return psycopg.connect(url, connect_timeout=15)
            except Exception as exc:
                last_err = exc
                if i < retries - 1:
                    time.sleep(sleep_s)
    
    raise RuntimeError(f"DB connect failed after {retries} attempts: {last_err}")


def ensure_schema(conn: psycopg.Connection) -> None:
    """Ensure the coconino_leads table exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS coconino_leads (
                id               BIGSERIAL PRIMARY KEY,
                source_county    TEXT NOT NULL DEFAULT 'Coconino',
                document_id      TEXT NOT NULL,
                recording_number TEXT,
                recording_date   TEXT,
                document_type    TEXT,
                grantors         TEXT,
                grantees         TEXT,
                trustor          TEXT,
                trustee          TEXT,
                beneficiary      TEXT,
                principal_amount TEXT,
                property_address TEXT,
                legal_description TEXT,
                detail_url       TEXT,
                document_url     TEXT,
                ocr_text_path    TEXT,
                used_groq        BOOLEAN,
                groq_error       TEXT,
                metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (source_county, document_id)
            );
            """
        )
        
        # Add columns if they don't exist (migrations)
        cols = [
            ("source_county", "TEXT NOT NULL DEFAULT 'Coconino'"),
            ("recording_number", "TEXT"),
            ("recording_date", "TEXT"),
            ("document_type", "TEXT"),
            ("grantors", "TEXT"),
            ("grantees", "TEXT"),
            ("trustor", "TEXT"),
            ("trustee", "TEXT"),
            ("beneficiary", "TEXT"),
            ("principal_amount", "TEXT"),
            ("property_address", "TEXT"),
            ("legal_description", "TEXT"),
            ("detail_url", "TEXT"),
            ("document_url", "TEXT"),
            ("ocr_text_path", "TEXT"),
            ("used_groq", "BOOLEAN"),
            ("groq_error", "TEXT"),
            ("metadata", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
        ]
        
        for col_name, col_def in cols:
            cur.execute(f"ALTER TABLE coconino_leads ADD COLUMN IF NOT EXISTS {col_name} {col_def};")
            
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS coconino_leads_source_document_uidx
            ON coconino_leads (source_county, document_id);
            """
        )
    conn.commit()


def upsert_records(conn: psycopg.Connection, records: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert records into coconino_leads table."""
    inserted = 0
    updated = 0
    
    with conn.cursor() as cur:
        for r in records:
            doc_id = str(r.get("documentId", "")).strip()
            if not doc_id:
                # Generate synthetic ID if missing
                basis = "|".join([
                    str(r.get("detailUrl", "")),
                    str(r.get("recordingNumber", "")),
                    str(r.get("recordingDate", "")),
                    str(r.get("documentType", ""))
                ])
                digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
                doc_id = f"synthetic:{digest}"
            
            # Helper to join lists
            def join_list(val: Any) -> str:
                if isinstance(val, list):
                    return " | ".join(str(x) for x in val if x)
                return str(val or "")

            payload = {
                "source_county": "Coconino",
                "document_id": doc_id,
                "recording_number": r.get("recordingNumber", ""),
                "recording_date": r.get("recordingDate", ""),
                "document_type": r.get("documentType", ""),
                "grantors": join_list(r.get("grantors", [])),
                "grantees": join_list(r.get("grantees", [])),
                "trustor": r.get("trustor", ""),
                "trustee": r.get("trustee", ""),
                "beneficiary": r.get("beneficiary", ""),
                "principal_amount": r.get("principalAmount", ""),
                "property_address": r.get("propertyAddress", ""),
                "legal_description": join_list(r.get("legalDescriptions", [])),
                "detail_url": r.get("detailUrl", ""),
                "document_url": r.get("documentUrl", ""),
                "ocr_text_path": r.get("ocrTextPath", ""),
                "used_groq": bool(r.get("usedGroq", False)),
                "groq_error": r.get("groqError", ""),
                "metadata": Jsonb(r)
            }
            
            cur.execute(
                """
                INSERT INTO coconino_leads (
                    source_county, document_id, recording_number, recording_date, document_type,
                    grantors, grantees, trustor, trustee, beneficiary, principal_amount,
                    property_address, legal_description, detail_url, document_url,
                    ocr_text_path, used_groq, groq_error, metadata
                ) VALUES (
                    %(source_county)s, %(document_id)s, %(recording_number)s, %(recording_date)s, %(document_type)s,
                    %(grantors)s, %(grantees)s, %(trustor)s, %(trustee)s, %(beneficiary)s, %(principal_amount)s,
                    %(property_address)s, %(legal_description)s, %(detail_url)s, %(document_url)s,
                    %(ocr_text_path)s, %(used_groq)s, %(groq_error)s, %(metadata)s
                )
                ON CONFLICT (source_county, document_id) DO UPDATE SET
                    recording_number = EXCLUDED.recording_number,
                    recording_date = EXCLUDED.recording_date,
                    document_type = EXCLUDED.document_type,
                    grantors = EXCLUDED.grantors,
                    grantees = EXCLUDED.grantees,
                    trustor = EXCLUDED.trustor,
                    trustee = EXCLUDED.trustee,
                    beneficiary = EXCLUDED.beneficiary,
                    principal_amount = EXCLUDED.principal_amount,
                    property_address = EXCLUDED.property_address,
                    legal_description = EXCLUDED.legal_description,
                    detail_url = EXCLUDED.detail_url,
                    document_url = EXCLUDED.document_url,
                    ocr_text_path = EXCLUDED.ocr_text_path,
                    used_groq = EXCLUDED.used_groq,
                    groq_error = EXCLUDED.groq_error,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING (xmax = 0) AS inserted;
                """,
                payload
            )
            
            row = cur.fetchone()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1
                
    conn.commit()
    return inserted, updated
