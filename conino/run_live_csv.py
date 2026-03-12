from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from extractor import search_to_csv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--csv-name", default="coconino_live_results.csv")
    parser.add_argument("--json-name", default="coconino_live_results.json")
    parser.add_argument("--document-types", default="")
    parser.add_argument("--include-document-analysis", action="store_true")
    parser.add_argument("--document-limit", type=int, default=0)
    parser.add_argument("--use-groq", action="store_true")
    parser.add_argument("--save-html", action="store_true")
    args = parser.parse_args()

    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        raise RuntimeError("Set COCONINO_COOKIE before running this script")

    document_types = [item.strip() for item in args.document_types.split(",") if item.strip()] or None
    result = search_to_csv(
        start_date=args.start_date,
        end_date=args.end_date,
        document_types=document_types,
        use_groq=args.use_groq,
        csv_name=args.csv_name,
        include_document_analysis=args.include_document_analysis,
        document_limit=args.document_limit,
        save_html=args.save_html,
        cookie=cookie,
    )

    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / args.json_name
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": result.get("ok"),
                "dataSource": result.get("dataSource"),
                "recordCount": result.get("recordCount"),
                "summary": result.get("summary"),
                "csvPath": result.get("csvPath"),
                "jsonPath": str(json_path),
                "htmlFiles": result.get("htmlFiles"),
                "warnings": result.get("warnings"),
                "liveError": result.get("liveError"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
