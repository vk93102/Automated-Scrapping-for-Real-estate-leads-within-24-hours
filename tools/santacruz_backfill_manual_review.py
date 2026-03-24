from __future__ import annotations

import argparse
import re
from pathlib import Path

import psycopg


def _compute(raw: dict) -> tuple[bool, str, str]:
    not_found_fields: list[str] = []
    # Lead-critical fields for review.
    for k in ["trustor", "principalAmount", "propertyAddress"]:
        if str(raw.get(k, "") or "").strip() == "NOT_FOUND":
            not_found_fields.append(k)

    groq_err = str(raw.get("groqError", "") or "").strip()

    # Santa Cruz request: flag manual review primarily for NOT_FOUND fields (or extraction errors).
    manual = bool(not_found_fields or groq_err)

    reasons: list[str] = []
    if manual:
        if not_found_fields:
            reasons.append("NOT_FOUND:" + ",".join(not_found_fields))
        if groq_err:
            reasons.append("GROQ_ERROR")
        if str(raw.get("analysisError", "") or "").strip():
            reasons.append("ANALYSIS_ERROR")

    trustor = str(raw.get("trustor", "") or "").strip()
    beneficiary = str(raw.get("beneficiary", "") or "").strip()
    addr = str(raw.get("propertyAddress", "") or "").strip()
    amt = str(raw.get("principalAmount", "") or "").strip()
    summary = f"trustor={trustor}; beneficiary={beneficiary}; address={addr}; principal={amt}" if manual else ""

    return manual, " | ".join(reasons), summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db-url-file", default=".supabase_database_url")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db_url = Path(args.db_url_file).read_text(encoding="utf-8", errors="ignore").strip()
    if not db_url:
        raise SystemExit("Empty db url")
    if "sslmode=" not in db_url.lower():
        db_url = f"{db_url}{'&' if '?' in db_url else '?'}sslmode=require"

    updated = 0
    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute("select document_id, raw_record from santacruz_leads;")
            rows = cur.fetchall()

        with conn.cursor() as cur:
            for document_id, raw_record in rows:
                raw = raw_record or {}
                manual, reasons, summary = _compute(raw)

                # Keep the API (which reads from raw_record) consistent with the
                # materialized columns.
                raw["manualReview"] = bool(manual)
                raw["manualReviewReasons"] = reasons
                raw["manualReviewSummary"] = summary

                if args.dry_run:
                    continue

                cur.execute(
                    """
                    update santacruz_leads
                    set manual_review=%s,
                        manual_review_reasons=%s,
                        manual_review_summary=%s,
                        raw_record=%s,
                        updated_at=now()
                    where document_id=%s;
                    """,
                    (manual, reasons, summary, psycopg.types.json.Jsonb(raw), str(document_id)),
                )
                updated += 1

        if not args.dry_run:
            conn.commit()

    print(f"rows_total={len(rows)}")
    print(f"rows_updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
