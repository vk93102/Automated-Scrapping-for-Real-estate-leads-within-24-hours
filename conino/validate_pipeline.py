from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from api import CoconinoHandler
from extractor import (
    default_last_three_month_range,
    fetch_document_detail_fields,
    fetch_document_ocr_and_analysis,
    fetch_document_pdf,
    parse_search_results_html,
)
from fetch_with_session import OUTPUT_DIR, run_automation

REPORT_PATH = OUTPUT_DIR / "component_check_report.json"


def _load_cookie_header_from_state(state_file: str) -> str:
    path = Path(state_file)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    cookies = payload.get("cookies") or []
    parts: list[str] = []
    for item in cookies:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _bool_result(name: str, passed: bool, details: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "details": details or {},
        "error": error,
    }


def _run_api_check(path: str, cookie_header: str = "", timeout: int = 300) -> dict[str, Any]:
    port = 8786
    base_url = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), CoconinoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {}
        if cookie_header.strip():
            headers["X-Coconino-Cookie"] = cookie_header.strip()
        request = Request(f"{base_url}{path}", headers=headers)
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_validation() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    start_date, end_date = default_last_three_month_range()

    checks: list[dict[str, Any]] = []
    sample_record: dict[str, Any] = {}
    pdf_working_record: dict[str, Any] = {}
    cookie_header = ""
    automation_result: dict[str, Any] = {}

    original_cookie = os.environ.pop("COCONINO_COOKIE", None)
    try:
        try:
            automation_result = run_automation(
                start_date=start_date,
                end_date=end_date,
                csv_name="coconino_validation.csv",
                json_name="coconino_validation.json",
                detail_max_records=5,
                ocr_principal_limit=1,
                headless=True,
            )
            checks.append(
                _bool_result(
                    "playwright_bootstrap_without_manual_cookie",
                    bool(automation_result.get("ok")),
                    {
                        "sessionMode": automation_result.get("sessionMode"),
                        "recordCount": automation_result.get("recordCount"),
                        "stateFile": automation_result.get("stateFile"),
                    },
                )
            )
        except Exception as exc:
            checks.append(
                _bool_result(
                    "playwright_bootstrap_without_manual_cookie",
                    False,
                    {},
                    str(exc),
                )
            )
            raise

        cookie_header = _load_cookie_header_from_state(str(automation_result.get("stateFile") or ""))
        checks.append(
            _bool_result(
                "session_cookie_available_from_state",
                bool(cookie_header.strip()),
                {
                    "cookieLength": len(cookie_header),
                },
                "No cookies found in state file" if not cookie_header.strip() else "",
            )
        )

        html_path = Path(str(automation_result.get("htmlPath") or ""))
        html_text = html_path.read_text(encoding="utf-8", errors="ignore") if html_path.exists() else ""
        parsed = parse_search_results_html(html_text, source_file=html_path.name if html_path.exists() else "")
        records = parsed.get("records") or []
        sample_record = records[0] if records else {}

        has_id = bool(str(sample_record.get("documentId", "")).strip())
        has_detail_url = bool(str(sample_record.get("detailUrl", "")).strip())
        checks.append(
            _bool_result(
                "discovery_step_documentid_and_detailurl",
                bool(records) and has_id and has_detail_url,
                {
                    "parsedRecords": len(records),
                    "sampleDocumentId": sample_record.get("documentId"),
                    "sampleDetailUrl": sample_record.get("detailUrl"),
                },
                "" if (bool(records) and has_id and has_detail_url) else "No records or missing documentId/detailUrl",
            )
        )

        if has_id and cookie_header.strip():
            try:
                detail = fetch_document_detail_fields(str(sample_record.get("documentId")), cookie=cookie_header)
                checks.append(
                    _bool_result(
                        "detail_endpoint_reachable",
                        bool(detail.get("detailHtmlLength", 0) > 0),
                        {
                            "detailUrl": detail.get("detailUrl"),
                            "detailHtmlLength": detail.get("detailHtmlLength"),
                            "propertyAddress": detail.get("propertyAddress"),
                            "principalAmount": detail.get("principalAmount"),
                        },
                    )
                )
            except Exception as exc:
                checks.append(_bool_result("detail_endpoint_reachable", False, {}, str(exc)))
        else:
            checks.append(
                _bool_result(
                    "detail_endpoint_reachable",
                    False,
                    {},
                    "Missing sample documentId or state cookie",
                )
            )

        candidate_records = [
            r
            for r in records[:25]
            if str(r.get("documentId", "")).strip() and str(r.get("recordingNumber", "")).strip()
        ]

        pdf_ok = False
        pdf_error = ""
        pdf_details: dict[str, Any] = {"attemptedRecords": len(candidate_records)}
        if candidate_records and cookie_header.strip():
            for candidate in candidate_records:
                try:
                    pdf = fetch_document_pdf(
                        document_id=str(candidate.get("documentId")),
                        recording_number=str(candidate.get("recordingNumber")),
                        index=1,
                        cookie=cookie_header,
                    )
                    pdf_path = Path(str(pdf.get("pdfPath", "")))
                    if pdf_path.exists() and pdf_path.stat().st_size > 0:
                        pdf_ok = True
                        pdf_working_record = dict(candidate)
                        pdf_details = {
                            "attemptedRecords": len(candidate_records),
                            "documentId": candidate.get("documentId"),
                            "recordingNumber": candidate.get("recordingNumber"),
                            "pdfUrl": pdf.get("pdfUrl"),
                            "pdfPath": str(pdf_path),
                            "pdfSizeBytes": pdf_path.stat().st_size,
                        }
                        break
                    pdf_error = f"Downloaded empty PDF for {candidate.get('documentId')}"
                except Exception as exc:
                    pdf_error = str(exc)
                    continue
        else:
            pdf_error = "Missing candidate records or state cookie"

        checks.append(_bool_result("pdf_download_reachable", pdf_ok, pdf_details, "" if pdf_ok else pdf_error))

        ocr_ok = False
        ocr_error = ""
        ocr_details: dict[str, Any] = {}
        ocr_candidate = pdf_working_record or (candidate_records[0] if candidate_records else {})
        if ocr_candidate and cookie_header.strip():
            try:
                analysis = fetch_document_ocr_and_analysis(
                    document_id=str(ocr_candidate.get("documentId")),
                    recording_number=str(ocr_candidate.get("recordingNumber")),
                    index=1,
                    document_type=str(ocr_candidate.get("documentType", "")),
                    cookie=cookie_header,
                    use_groq=False,
                )
                ocr_ok = int(analysis.get("ocrTextLength") or 0) >= 0
                ocr_details = {
                    "documentId": ocr_candidate.get("documentId"),
                    "recordingNumber": ocr_candidate.get("recordingNumber"),
                    "ocrMethod": analysis.get("ocrMethod"),
                    "ocrTextLength": analysis.get("ocrTextLength"),
                    "addressCandidates": len(analysis.get("addressCandidates") or []),
                    "principalCandidates": len(analysis.get("principalCandidates") or []),
                }
            except Exception as exc:
                ocr_error = str(exc)
        else:
            ocr_error = "Missing prerequisites for OCR check"

        checks.append(_bool_result("ocr_workable", ocr_ok, ocr_details, "" if ocr_ok else ocr_error))

        try:
            query = urlencode(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "use_current_session_results": "true",
                    "save_html": "false",
                    "document_limit": "0",
                }
            )
            payload = _run_api_check(f"/search?{query}", cookie_header=cookie_header, timeout=360)
            checks.append(
                _bool_result(
                    "basic_search_api_response",
                    bool(payload.get("ok")),
                    {
                        "recordCount": payload.get("recordCount"),
                        "dataSource": payload.get("dataSource"),
                    },
                    "Search API returned non-ok payload" if not payload.get("ok") else "",
                )
            )
        except Exception as exc:
            checks.append(_bool_result("basic_search_api_response", False, {}, str(exc)))

        api_doc_id = str((pdf_working_record or sample_record).get("documentId", "")).strip()
        api_recording_number = str((pdf_working_record or sample_record).get("recordingNumber", "")).strip()
        api_document_type = str((pdf_working_record or sample_record).get("documentType", "")).strip()

        if api_doc_id and api_recording_number:
            try:
                query = urlencode(
                    {
                        "document_id": api_doc_id,
                        "recording_number": api_recording_number,
                        "index": "1",
                        "document_type": api_document_type,
                        "use_groq": "false",
                    }
                )
                payload = _run_api_check(f"/document?{query}", cookie_header=cookie_header, timeout=360)
                checks.append(
                    _bool_result(
                        "sub_document_api_response",
                        bool(payload.get("ok")),
                        {
                            "documentId": payload.get("documentId"),
                            "recordingNumber": payload.get("recordingNumber"),
                            "ocrMethod": payload.get("ocrMethod"),
                            "ocrTextLength": payload.get("ocrTextLength"),
                        },
                        "Document API returned non-ok payload" if not payload.get("ok") else "",
                    )
                )
            except Exception as exc:
                checks.append(_bool_result("sub_document_api_response", False, {}, str(exc)))
        else:
            checks.append(_bool_result("sub_document_api_response", False, {}, "Missing sample document information"))

    finally:
        if original_cookie is not None:
            os.environ["COCONINO_COOKIE"] = original_cookie

    passed = sum(1 for item in checks if item.get("passed"))
    failed = len(checks) - passed

    report = {
        "ok": failed == 0,
        "generatedAt": datetime.now().isoformat(),
        "checkSummary": {
            "total": len(checks),
            "passed": passed,
            "failed": failed,
        },
        "automation": {
            "recordCount": automation_result.get("recordCount"),
            "sessionMode": automation_result.get("sessionMode"),
            "jsonPath": automation_result.get("jsonPath"),
            "htmlPath": automation_result.get("htmlPath"),
        },
        "sampleRecord": {
            "documentId": sample_record.get("documentId"),
            "recordingNumber": sample_record.get("recordingNumber"),
            "detailUrl": sample_record.get("detailUrl"),
        },
        "checklist": checks,
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    report = run_validation()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nSaved report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
