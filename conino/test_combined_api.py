from __future__ import annotations

import json
import os
from threading import Thread
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from api import CoconinoHandler

COOKIE = os.environ.get("COCONINO_COOKIE", "").strip()
BASE_URL = "http://127.0.0.1:8774"


def main() -> None:
    if not COOKIE:
        raise RuntimeError("Set COCONINO_COOKIE before running this test")
    server = ThreadingHTTPServer(("127.0.0.1", 8774), CoconinoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            (
                f"{BASE_URL}/extract?html_file=search_results_ajax_20260312_212603.html"
                f"&limit=1&use_groq=true&include_document_analysis=true&document_limit=1"
            ),
            headers={"X-Coconino-Cookie": COOKIE},
        )
        with urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
        first_record = (payload.get("records") or [{}])[0]
        print(
            json.dumps(
                {
                    "ok": payload.get("ok"),
                    "recordCount": payload.get("recordCount"),
                    "documentId": first_record.get("documentId"),
                    "recordingNumber": first_record.get("recordingNumber"),
                    "documentAnalysis": first_record.get("documentAnalysis"),
                    "documentAnalysisError": first_record.get("documentAnalysisError"),
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
