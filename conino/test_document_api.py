from __future__ import annotations

import json
import os
from threading import Thread
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from api import CoconinoHandler

COOKIE = os.environ.get("COCONINO_COOKIE", "").strip()
BASE_URL = "http://127.0.0.1:8773"


def main() -> None:
    if not COOKIE:
        raise RuntimeError("Set COCONINO_COOKIE before running this test")
    server = ThreadingHTTPServer(("127.0.0.1", 8773), CoconinoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"{BASE_URL}/document?document_id=DOC1870S955&recording_number=4035083&index=1&document_type=LIS%20PENDENS&use_groq=true",
            headers={"X-Coconino-Cookie": COOKIE},
        )
        with urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
        print(
            json.dumps(
                {
                    "ok": payload.get("ok"),
                    "documentId": payload.get("documentId"),
                    "recordingNumber": payload.get("recordingNumber"),
                    "ocrMethod": payload.get("ocrMethod"),
                    "ocrTextLength": payload.get("ocrTextLength"),
                    "ocrTextPreview": payload.get("ocrTextPreview"),
                    "usedGroq": payload.get("usedGroq"),
                    "groqError": payload.get("groqError"),
                    "pdfPath": payload.get("pdfPath"),
                    "ocrTextPath": payload.get("ocrTextPath"),
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
