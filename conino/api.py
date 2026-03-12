from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from extractor import (
    OUTPUT_DIR,
    available_html_files,
    default_last_three_month_range,
    extract_to_csv,
    fetch_document_ocr_and_analysis,
    load_env,
    search_to_csv,
)


class CoconinoHandler(BaseHTTPRequestHandler):
    server_version = "CoconinoAPI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "service": "conino-api"})
                return
            if path == "/html-files":
                self._send_json(HTTPStatus.OK, {"ok": True, "files": available_html_files()})
                return
            if path == "/search":
                default_start, default_end = default_last_three_month_range()
                result = search_to_csv(
                    start_date=self._single(query, "start_date") or default_start,
                    end_date=self._single(query, "end_date") or default_end,
                    document_types=self._list_value(query, "document_types"),
                    use_groq=self._bool_value(query, "use_groq", default=True),
                    csv_name=self._single(query, "csv_name"),
                    include_document_analysis=self._bool_value(query, "include_document_analysis", default=False),
                    document_limit=self._int_value(query, "document_limit", default=0) or 0,
                    document_index=self._int_value(query, "document_index", default=1) or 1,
                    page_limit=self._int_value(query, "page_limit"),
                    cookie=self._cookie_header(),
                    save_html=self._bool_value(query, "save_html", default=True),
                    use_current_session_results=self._bool_value(query, "use_current_session_results", default=False),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/extract":
                html_file = self._single(query, "html_file")
                if not html_file:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "html_file is required"})
                    return
                limit = self._int_value(query, "limit")
                offset = self._int_value(query, "offset", default=0) or 0
                use_groq = self._bool_value(query, "use_groq", default=True)
                csv_name = self._single(query, "csv_name")
                document_types = self._list_value(query, "document_types")
                result = extract_to_csv(
                    html_file=html_file,
                    limit=limit,
                    offset=offset,
                    use_groq=use_groq,
                    csv_name=csv_name,
                    document_types=document_types,
                )
                include_document_analysis = self._bool_value(query, "include_document_analysis", default=False)
                document_limit = self._int_value(query, "document_limit", default=1) or 1
                if include_document_analysis and result.get("records"):
                    cookie = self._cookie_header()
                    enriched_records = []
                    for index, record in enumerate(result["records"]):
                        updated = dict(record)
                        if index < document_limit:
                            try:
                                updated["documentAnalysis"] = fetch_document_ocr_and_analysis(
                                    document_id=str(record.get("documentId", "")),
                                    recording_number=str(record.get("recordingNumber", "")),
                                    index=self._int_value(query, "document_index", default=1) or 1,
                                    document_type=str(record.get("documentType", "")),
                                    cookie=cookie,
                                    use_groq=use_groq,
                                )
                            except Exception as exc:
                                updated["documentAnalysisError"] = str(exc)
                        enriched_records.append(updated)
                    result["records"] = enriched_records
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/document":
                document_id = self._single(query, "document_id")
                recording_number = self._single(query, "recording_number")
                if not document_id or not recording_number:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "document_id and recording_number are required"},
                    )
                    return
                result = fetch_document_ocr_and_analysis(
                    document_id=document_id,
                    recording_number=recording_number,
                    index=self._int_value(query, "index", default=1) or 1,
                    document_type=self._single(query, "document_type") or "",
                    cookie=self._cookie_header(),
                    use_groq=self._bool_value(query, "use_groq", default=True),
                )
                self._send_json(HTTPStatus.OK, {"ok": True, **result})
                return
            if path == "/csv":
                filename = self._single(query, "name")
                if not filename:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "name is required"})
                    return
                target = (OUTPUT_DIR / filename).resolve()
                if OUTPUT_DIR.resolve() not in target.parents or not target.exists():
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "CSV not found"})
                    return
                self._send_file(target, "text/csv; charset=utf-8")
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Unknown path: {path}"})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _single(self, query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key, [])
        return values[0].strip() if values and values[0].strip() else None

    def _int_value(self, query: dict[str, list[str]], key: str, default: int | None = None) -> int | None:
        raw = self._single(query, key)
        if raw is None:
            return default
        return int(raw)

    def _bool_value(self, query: dict[str, list[str]], key: str, default: bool = False) -> bool:
        raw = self._single(query, key)
        if raw is None:
            return default
        return raw.lower() in {"1", "true", "yes", "y", "on"}

    def _list_value(self, query: dict[str, list[str]], key: str) -> list[str] | None:
        raw = self._single(query, key)
        if not raw:
            return None
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _cookie_header(self) -> str | None:
        value = self.headers.get("X-Coconino-Cookie", "").strip()
        return value or None

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server() -> None:
    load_env()
    host = os.environ.get("COCONINO_API_HOST", "127.0.0.1")
    port = int(os.environ.get("COCONINO_API_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), CoconinoHandler)
    print(json.dumps({"ok": True, "host": host, "port": port, "service": "conino-api"}))
    server.serve_forever()


if __name__ == "__main__":
    run_server()
