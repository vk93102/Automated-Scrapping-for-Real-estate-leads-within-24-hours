from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_URL = f"{BASE_URL}/web/search/DOCSEARCH1213S1"
EXPORT_URL = f"{BASE_URL}/web/viewSearchResultsReport/DOCSEARCH1213S1/CSV"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        raise RuntimeError("Set COCONINO_COOKIE before running this script")

    opener = build_opener(HTTPCookieProcessor())
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": SEARCH_URL,
        "Connection": "keep-alive",
        "Cookie": cookie,
    }

    with opener.open(Request(SEARCH_URL, headers=headers), timeout=60) as response:
        search_html = response.read().decode("utf-8", errors="ignore")
        (OUTPUT_DIR / "export_search_page.html").write_text(search_html, encoding="utf-8")

    try:
        export_headers = dict(headers)
        export_headers["Accept"] = "text/csv,*/*;q=0.8"
        export_headers["Referer"] = SEARCH_URL
        with opener.open(Request(EXPORT_URL, headers=export_headers), timeout=60) as response:
            body = response.read()
            content_type = response.headers.get("content-type", "")
            path = OUTPUT_DIR / "county_export_live.csv"
            path.write_bytes(body)
            print(json.dumps({"status": response.status, "contentType": content_type, "path": str(path), "size": len(body)}, indent=2))
    except HTTPError as exc:
        body = exc.read()
        path = OUTPUT_DIR / "county_export_live_error.html"
        path.write_bytes(body)
        print(json.dumps({"status": exc.code, "path": str(path), "size": len(body)}, indent=2))


if __name__ == "__main__":
    main()
