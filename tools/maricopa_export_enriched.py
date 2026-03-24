from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import psycopg


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1]
        os.environ.setdefault(k, v)


from maricopa.csv_export import write_csv


def _schema_name() -> str:
    raw = (os.environ.get("MARICOPA_DB_SCHEMA") or "maricopa").strip()
    if not raw:
        return "maricopa"
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", raw):
        raise SystemExit(f"Invalid MARICOPA_DB_SCHEMA: {raw!r}")
    return raw


def _coerce_json(v: Any) -> dict[str, Any]:
    if v in (None, ""):
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", errors="replace")
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return {}
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


def _fetch_enriched(conn: psycopg.Connection, recording_numbers: list[str]) -> list[dict[str, Any]]:
    rec_list = [str(r).strip() for r in (recording_numbers or []) if str(r).strip()]
    if not rec_list:
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            select
              d.recording_number,
              d.recording_date,
              d.document_type,
              d.page_amount,
              d.names,
              d.metadata,
              (d.ocr_text is not null and length(d.ocr_text) > 0) as ocr_text_present,
                            p.document_url,
              p.trustor_1_full_name, p.trustor_1_first_name, p.trustor_1_last_name,
              p.trustor_2_full_name, p.trustor_2_first_name, p.trustor_2_last_name,
              p.address_city, p.address_state, p.address_zip,
              p.property_address, p.address_unit,
              p.sale_date,
              p.original_principal_balance,
              p.llm_model
            from documents d
            left join properties p on p.document_id = d.id
            where d.recording_number = any(%s)
            order by d.recording_number
            """,
            (rec_list,),
        )
        rows = cur.fetchall() or []

    out: list[dict[str, Any]] = []
    for r in rows:
        meta_json = _coerce_json(r[5])
        doc_codes = meta_json.get("document_codes") or meta_json.get("documentCodes")
        if not doc_codes and r[2]:
            doc_codes = [r[2]]

        names_val = meta_json.get("names")
        if not names_val and r[4]:
            names_val = [x.strip() for x in str(r[4]).split(",") if x.strip()]

        out.append(
            {
                "recordingNumber": r[0],
                "recordingDate": r[1],
                "documentCodes": doc_codes,
                "pageAmount": r[3],
                "names": names_val,
                "restricted": meta_json.get("restricted"),
                "metadata": meta_json,
                "ocrTextPresent": bool(r[6]),
                "document_url": r[7],
                "trustor_1_full_name": r[8],
                "trustor_1_first_name": r[9],
                "trustor_1_last_name": r[10],
                "trustor_2_full_name": r[11],
                "trustor_2_first_name": r[12],
                "trustor_2_last_name": r[13],
                "address_city": r[14],
                "address_state": r[15],
                "address_zip": r[16],
                "property_address": r[17],
                "address_unit": r[18],
                "sale_date": r[19],
                "original_principal_balance": r[20],
                "llm_model": r[21],
            }
        )

    return out


def _fetch_all_enriched(conn: psycopg.Connection, *, limit: int = 0) -> list[dict[str, Any]]:
    limit_sql = ""
    if limit and int(limit) > 0:
        limit_sql = f"\nlimit {int(limit)}"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            select
              d.recording_number,
              d.recording_date,
              d.document_type,
              d.page_amount,
              d.names,
              d.metadata,
              (d.ocr_text is not null and length(d.ocr_text) > 0) as ocr_text_present,
              p.document_url,
              p.trustor_1_full_name, p.trustor_1_first_name, p.trustor_1_last_name,
              p.trustor_2_full_name, p.trustor_2_first_name, p.trustor_2_last_name,
              p.address_city, p.address_state, p.address_zip,
              p.property_address, p.address_unit,
              p.sale_date,
              p.original_principal_balance,
              p.llm_model
            from documents d
            left join properties p on p.document_id = d.id
            order by d.recording_number
            {limit_sql}
            """
        )
        rows = cur.fetchall() or []

    out: list[dict[str, Any]] = []
    for r in rows:
        meta_json = _coerce_json(r[5])
        doc_codes = meta_json.get("document_codes") or meta_json.get("documentCodes")
        if not doc_codes and r[2]:
            doc_codes = [r[2]]

        names_val = meta_json.get("names")
        if not names_val and r[4]:
            names_val = [x.strip() for x in str(r[4]).split(",") if x.strip()]

        out.append(
            {
                "recordingNumber": r[0],
                "recordingDate": r[1],
                "documentCodes": doc_codes,
                "pageAmount": r[3],
                "names": names_val,
                "restricted": meta_json.get("restricted"),
                "metadata": meta_json,
                "ocrTextPresent": bool(r[6]),
                "document_url": r[7],
                "trustor_1_full_name": r[8],
                "trustor_1_first_name": r[9],
                "trustor_1_last_name": r[10],
                "trustor_2_full_name": r[11],
                "trustor_2_first_name": r[12],
                "trustor_2_last_name": r[13],
                "address_city": r[14],
                "address_state": r[15],
                "address_zip": r[16],
                "property_address": r[17],
                "address_unit": r[18],
                "sale_date": r[19],
                "original_principal_balance": r[20],
                "llm_model": r[21],
            }
        )
    return out


def _read_recording_numbers_from_pipeline_json(p: Path) -> list[str]:
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected a JSON list")
    recs: list[str] = []
    for row in data:
        if isinstance(row, dict) and row.get("recordingNumber"):
            recs.append(str(row["recordingNumber"]).strip())
    return [r for r in recs if r]


def main() -> None:
    ap = argparse.ArgumentParser(description="Export Maricopa enriched fields from Postgres")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    ap.add_argument("--in-json", default="output/pipeline_latest.json", help="Input pipeline JSON containing recordingNumber")
    ap.add_argument("--out-json", default="output/pipeline_latest_enriched.json", help="Enriched JSON output")
    ap.add_argument("--out-csv", default="output/pipeline_latest_enriched.csv", help="Enriched CSV output")
    ap.add_argument("--include-meta", action="store_true", help="Include Recording Number/Date/Type/Pages columns in CSV")
    ap.add_argument("--all", action="store_true", help="Export all documents from DB (ignores --in-json)")
    ap.add_argument("--limit", type=int, default=0, help="Optional limit when using --all")
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = args.db_url or os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    with psycopg.connect(db_url) as conn:
        schema = _schema_name()
        with conn.cursor() as cur:
            cur.execute(f"set search_path to {schema}, public;")
        conn.commit()

        if args.all:
            rows = _fetch_all_enriched(conn, limit=int(args.limit or 0))
        else:
            in_path = Path(args.in_json)
            recs = _read_recording_numbers_from_pipeline_json(in_path)
            if not recs:
                raise SystemExit(f"No recordingNumber values found in {in_path}")
            rows = _fetch_enriched(conn, recs)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_csv(args.out_csv, rows, include_meta=bool(args.include_meta))

    print(str(out_json))
    print(str(Path(args.out_csv)))
    print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
