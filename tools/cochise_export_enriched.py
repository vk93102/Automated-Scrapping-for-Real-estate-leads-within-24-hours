from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

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


CSV_HEADERS = [
    "document_id",
    "recording_number",
    "recording_date",
    "document_type",
    "trustor",
    "trustee",
    "beneficiary",
    "principal_amount",
    "property_address",
    "detail_url",
    "image_urls",
    "ocr_method",
    "ocr_chars",
    "used_groq",
    "groq_model",
    "groq_error",
    "analysis_error",
    "run_date",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Export Cochise leads from Postgres")
    ap.add_argument("--dotenv", default=".env", help="Optional .env file (default: .env)")
    ap.add_argument("--db-url", default="", help="Postgres connection string (defaults from DATABASE_URL)")
    ap.add_argument("--out-json", default="output/supabase_cochise_last_2_weeks.json")
    ap.add_argument("--out-csv", default="output/supabase_cochise_last_2_weeks.csv")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    _load_dotenv(REPO_ROOT / str(args.dotenv))
    db_url = (args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("Missing --db-url (or DATABASE_URL)")

    limit_sql = ""
    if args.limit and int(args.limit) > 0:
        limit_sql = f"\nlimit {int(args.limit)}"

    with psycopg.connect(db_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                  document_id,
                  recording_number,
                  recording_date,
                  document_type,
                  trustor,
                  trustee,
                  beneficiary,
                  principal_amount,
                  property_address,
                  detail_url,
                  image_urls,
                  ocr_method,
                  ocr_chars,
                  used_groq,
                  groq_model,
                  groq_error,
                  analysis_error,
                  run_date
                from public.cochise_leads
                order by run_date desc, updated_at desc
                {limit_sql}
                """
            )
            rows = cur.fetchall() or []

    out: list[dict[str, object]] = []
    for r in rows:
        out.append(
            {
                "document_id": r[0],
                "recording_number": r[1],
                "recording_date": r[2],
                "document_type": r[3],
                "trustor": r[4],
                "trustee": r[5],
                "beneficiary": r[6],
                "principal_amount": r[7],
                "property_address": r[8],
                "detail_url": r[9],
                "image_urls": r[10],
                "ocr_method": r[11],
                "ocr_chars": r[12],
                "used_groq": bool(r[13]),
                "groq_model": r[14],
                "groq_error": r[15],
                "analysis_error": r[16],
                "run_date": str(r[17]) if r[17] is not None else "",
            }
        )

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writeheader()
        for row in out:
            w.writerow(row)

    print(str(out_json))
    print(str(out_csv))
    print(f"rows={len(out)}")


if __name__ == "__main__":
    main()
