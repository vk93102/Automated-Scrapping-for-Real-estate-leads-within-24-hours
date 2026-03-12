from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from api import CoconinoHandler

ROOT_DIR = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:8766"
HTML_FILE = "search_results_ajax_20260312_212603.html"
CSV_NAME = "meaningful_results_test.csv"


def wait_for_health(timeout_s: int = 15) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urlopen(f"{BASE_URL}/health", timeout=3) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("ok"):
                return
        except URLError:
            time.sleep(0.25)
    raise RuntimeError("API did not become healthy in time")


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8766), CoconinoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_for_health()
        query = urlencode(
            {
                "html_file": HTML_FILE,
                "limit": "8",
                "use_groq": "true",
                "csv_name": CSV_NAME,
            }
        )
        with urlopen(f"{BASE_URL}/extract?{query}", timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
        csv_path = ROOT_DIR / "output" / CSV_NAME
        if not result.get("ok"):
            raise RuntimeError(f"Extraction failed: {result}")
        if result.get("recordCount", 0) < 1:
            raise RuntimeError(f"No records returned: {result}")
        if not csv_path.exists():
            raise RuntimeError(f"CSV was not created: {csv_path}")
        preview = result.get("records", [])[:2]
        print(json.dumps({
            "ok": True,
            "recordCount": result.get("recordCount"),
            "csvFile": result.get("csvFile"),
            "preview": preview,
        }, indent=2, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
