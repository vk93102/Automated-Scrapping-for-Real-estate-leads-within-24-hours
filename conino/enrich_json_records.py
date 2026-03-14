from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from extractor import enrich_records_with_detail_fields


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--max-records", type=int, default=0, help="0 means all")
    parser.add_argument("--cookie", default=None)
    args = parser.parse_args()

    input_path = Path(args.input_json).resolve()
    output_path = Path(args.output_json).resolve()

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    cookie = args.cookie or os.environ.get("COCONINO_COOKIE", "").strip() or None

    max_records = None if args.max_records <= 0 else args.max_records
    enriched = enrich_records_with_detail_fields(records, cookie=cookie, max_records=max_records)

    payload["records"] = enriched
    payload["recordCount"] = len(enriched)
    payload["enrichedDetails"] = True
    payload["enrichedMaxRecords"] = args.max_records

    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    non_empty_address = sum(1 for rec in enriched if str(rec.get("propertyAddress", "")).strip())
    non_empty_principal = sum(1 for rec in enriched if str(rec.get("principalAmount", "")).strip())
    print(
        json.dumps(
            {
                "ok": True,
                "input": str(input_path),
                "output": str(output_path),
                "recordCount": len(enriched),
                "nonEmptyPropertyAddress": non_empty_address,
                "nonEmptyPrincipalAmount": non_empty_principal,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
