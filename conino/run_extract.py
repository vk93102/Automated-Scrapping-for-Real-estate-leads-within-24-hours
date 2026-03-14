from __future__ import annotations

import argparse
import json
import os

from extractor import extract_to_csv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-file", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--csv-name", default=None)
    parser.add_argument("--use-groq", action="store_true")
    parser.add_argument("--document-types", default="")
    parser.add_argument("--cookie", default=None)
    parser.add_argument("--no-enrich-details", action="store_true")
    args = parser.parse_args()

    cookie = args.cookie or os.environ.get("COCONINO_COOKIE", "").strip() or None
    result = extract_to_csv(
        html_file=args.html_file,
        limit=args.limit,
        offset=args.offset,
        use_groq=args.use_groq,
        csv_name=args.csv_name,
        document_types=[item.strip() for item in args.document_types.split(",") if item.strip()] or None,
        cookie=cookie,
        enrich_details=not args.no_enrich_details,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
