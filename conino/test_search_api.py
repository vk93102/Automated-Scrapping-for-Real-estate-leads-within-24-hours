from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from api import CoconinoHandler

COOKIE = os.environ.get("COCONINO_COOKIE", "").strip()
BASE_URL = "http://127.0.0.1:8775"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2025-12-12")
    parser.add_argument("--end-date", default="2026-03-12")
    parser.add_argument("--csv-name", default="coconino_last_3_months.csv")
    parser.add_argument("--use-current-session-results", action="store_true")
    parser.add_argument("--include-document-analysis", action="store_true")
    parser.add_argument("--document-limit", type=int, default=0)
    parser.add_argument("--use-groq", action="store_true")
    parser.add_argument("--save-html", action="store_true")
    parser.add_argument("--document-types", default="")
    parser.add_argument("--page-limit", type=int, default=0)
    parser.add_argument("--output-json", default="search_test_result.json")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8775), CoconinoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        query_params = {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "csv_name": args.csv_name,
            "save_html": str(args.save_html).lower(),
            "use_groq": str(args.use_groq).lower(),
            "include_document_analysis": str(args.include_document_analysis).lower(),
            "document_limit": str(args.document_limit),
            "use_current_session_results": str(args.use_current_session_results).lower(),
        }
        if args.document_types.strip():
            query_params["document_types"] = args.document_types.strip()
        if args.page_limit > 0:
            query_params["page_limit"] = str(args.page_limit)
        query = urlencode(query_params)
        headers = {}
        if COOKIE:
            headers["X-Coconino-Cookie"] = COOKIE
        request = Request(f"{BASE_URL}/search?{query}", headers=headers)
        with urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
        output_path = OUTPUT_DIR / args.output_json
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": payload.get("ok"),
                    "dataSource": payload.get("dataSource"),
                    "usedFallback": (payload.get("source") or {}).get("usedFallback"),
                    "liveError": payload.get("liveError"),
                    "recordCount": payload.get("recordCount"),
                    "csvPath": payload.get("csvPath"),
                    "jsonPath": str(output_path),
                    "htmlFiles": payload.get("htmlFiles"),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
