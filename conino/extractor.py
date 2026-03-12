from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_URL = f"{BASE_URL}/web/search/DOCSEARCH1213S1"
SEARCH_POST_URL = f"{BASE_URL}/web/searchPost/DOCSEARCH1213S1"
SEARCH_RESULTS_URL = f"{BASE_URL}/web/searchResults/DOCSEARCH1213S1"
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
DOCUMENTS_DIR = OUTPUT_DIR / "documents"
DEFAULT_DOCUMENT_TYPES = [
    "LIS PENDENS",
    "LIS PENDENS RELEASE",
    "TRUSTEES DEED UPON SALE",
    "SHERIFFS DEED",
    "NOTICE OF TRUSTEES SALE",
    "TREASURERS DEED",
    "AMENDED STATE LIEN",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
]
DOCUMENT_TYPE_ALIASES = {
    "TRUSTEES DEED": "TRUSTEES DEED UPON SALE",
    "NOTICE OF TRUSTEE SALE": "NOTICE OF TRUSTEES SALE",
}
DEFAULT_MODEL_CANDIDATES = [
    os.environ.get("COCONINO_GROQ_MODEL", "llama-3.3-70b-versatile"),
    "llama-3.1-8b-instant",
]


@dataclass
class ExtractedRecord:
    document_id: str
    recording_number: str
    document_type: str
    recording_date: str
    grantors: list[str]
    grantees: list[str]
    legal_descriptions: list[str]
    property_address: str
    detail_url: str
    source_file: str
    raw_html: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "recordingNumber": self.recording_number,
            "documentType": self.document_type,
            "recordingDate": self.recording_date,
            "grantors": self.grantors,
            "grantees": self.grantees,
            "legalDescriptions": self.legal_descriptions,
            "propertyAddress": self.property_address,
            "detailUrl": self.detail_url,
            "sourceFile": self.source_file,
        }


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    candidates = [ROOT_DIR / ".env", ROOT_DIR.parent / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    for key, value in env.items():
        os.environ.setdefault(key, value)
    return env


def available_html_files() -> list[str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path.name for path in OUTPUT_DIR.glob("*.html"))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def default_last_three_month_range() -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=90)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _normalize_date(value: str) -> str:
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def _normalize_document_types(document_types: list[str] | None) -> list[str]:
    if not document_types:
        return list(DEFAULT_DOCUMENT_TYPES)
    requested = [DOCUMENT_TYPE_ALIASES.get(item.strip().upper(), item.strip()) for item in document_types if item.strip()]
    invalid = [item for item in requested if item.upper() not in {value.upper() for value in DEFAULT_DOCUMENT_TYPES}]
    if invalid:
        raise ValueError(f"Unsupported document types: {', '.join(invalid)}")
    allowed_lookup = {value.upper(): value for value in DEFAULT_DOCUMENT_TYPES}
    return [allowed_lookup[item.upper()] for item in requested]


def _search_headers(cookie: str, ajax: bool = False, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": os.environ.get(
            "COCONINO_USER_AGENT",
            "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        ),
        "Referer": SEARCH_URL,
        "Connection": "keep-alive",
    }
    if ajax:
        headers.update({"Accept": "*/*", "X-Requested-With": "XMLHttpRequest", "ajaxrequest": "true"})
    else:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _parse_recording_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for pattern in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def latest_saved_results_html() -> Path:
    patterns = ["search_results_ajax_*.html", "live_search_results_page_*.html", "session_results_page_*.html"]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(OUTPUT_DIR.glob(pattern))
    candidates = sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("No saved Coconino search results HTML found")
    return candidates[0]


def _filter_records(records: list[dict[str, Any]], start_date: str, end_date: str, document_types: list[str]) -> list[dict[str, Any]]:
    start_dt = datetime.strptime(_normalize_date(start_date), "%Y-%m-%d")
    end_dt = datetime.strptime(_normalize_date(end_date), "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    requested_types = {item.upper() for item in _normalize_document_types(document_types)}
    filtered: list[dict[str, Any]] = []
    for record in records:
        record_dt = _parse_recording_datetime(str(record.get("recordingDate", "")))
        record_type = str(record.get("documentType", "")).upper()
        if record_dt is None:
            continue
        if not (start_dt <= record_dt <= end_dt):
            continue
        if requested_types and record_type not in requested_types:
            continue
        filtered.append(record)
    return filtered


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        key = (
            str(record.get("documentId", "")).strip(),
            str(record.get("recordingNumber", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _build_search_response(
    *,
    start_date: str,
    end_date: str,
    document_types: list[str],
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    html_files: list[str],
    csv_path: Path,
    data_source: str,
    live_error: str,
    include_document_analysis: bool,
    document_limit: int,
    use_groq: bool,
    used_fallback: bool,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "singleEndpoint": "/search",
        "recordCount": len(records),
        "records": records,
        "summary": summary,
        "htmlFiles": html_files,
        "csvFile": csv_path.name,
        "csvPath": str(csv_path),
        "dataSource": data_source,
        "liveError": live_error,
        "requestedGroq": use_groq,
        "includeDocumentAnalysis": include_document_analysis,
        "documentLimit": document_limit,
        "request": {
            "startDate": start_date,
            "endDate": end_date,
            "documentTypes": document_types,
            "includeDocumentAnalysis": include_document_analysis,
            "documentLimit": document_limit,
            "useGroq": use_groq,
        },
        "source": {
            "mode": data_source,
            "usedFallback": used_fallback,
            "liveError": live_error,
            "htmlFiles": html_files,
        },
        "outputs": {
            "csvFile": csv_path.name,
            "csvPath": str(csv_path),
        },
        "stats": {
            "recordCount": len(records),
            "page": summary.get("page"),
            "pageCount": summary.get("pageCount"),
            "totalResults": summary.get("totalResults"),
        },
        "warnings": warnings or [],
    }
    return payload


def _save_live_html(prefix: str, body: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{_timestamp()}.html"
    path = OUTPUT_DIR / filename
    path.write_text(body, encoding="utf-8")
    return filename


def run_live_search(
    start_date: str,
    end_date: str,
    document_types: list[str] | None = None,
    page_limit: int | None = None,
    cookie: str | None = None,
    save_html: bool = True,
) -> dict[str, Any]:
    load_env()
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        raise RuntimeError("Coconino session cookie is required via X-Coconino-Cookie header or COCONINO_COOKIE env var")
    opener = build_opener(HTTPCookieProcessor())
    opener.open(Request(SEARCH_URL, headers=_search_headers(effective_cookie)), timeout=30).read()
    normalized_types = _normalize_document_types(document_types)
    payload: list[tuple[str, str]] = [
        ("field_rdate_DOT_StartDate", _normalize_date(start_date)),
        ("field_rdate_DOT_EndDate", _normalize_date(end_date)),
    ]
    for doc_type in normalized_types:
        payload.append(("field_selfservice_documentTypes", doc_type))
    post_request = Request(
        SEARCH_POST_URL,
        data=urlencode(payload).encode("utf-8"),
        headers=_search_headers(effective_cookie, content_type="application/x-www-form-urlencoded"),
        method="POST",
    )
    post_body = opener.open(post_request, timeout=60).read().decode("utf-8", errors="ignore")
    post_file = _save_live_html("live_search_post", post_body) if save_html else ""

    all_records: list[dict[str, Any]] = []
    html_files: list[str] = [post_file] if post_file else []
    summary: dict[str, Any] = {}
    current_page = 1
    while True:
        request = Request(
            f"{SEARCH_RESULTS_URL}?page={current_page}",
            headers=_search_headers(effective_cookie, ajax=True),
            method="GET",
        )
        body = opener.open(request, timeout=60).read().decode("utf-8", errors="ignore")
        source_name = _save_live_html(f"live_search_results_page_{current_page}", body) if save_html else f"live_page_{current_page}.html"
        if save_html:
            html_files.append(source_name)
        parsed = parse_search_results_html(body, source_file=source_name)
        page_summary = parsed.get("summary", {})
        summary = page_summary or summary
        records = parsed.get("records", [])
        if not records:
            break
        all_records.extend(records)
        total_pages = int(page_summary.get("pageCount") or current_page)
        if current_page >= total_pages:
            break
        if page_limit is not None and current_page >= page_limit:
            break
        current_page += 1

    summary = {
        **summary,
        "requestedStartDate": _normalize_date(start_date),
        "requestedEndDate": _normalize_date(end_date),
        "requestedDocumentTypes": normalized_types,
        "pagesFetched": current_page,
    }
    return {"summary": summary, "records": all_records, "htmlFiles": [name for name in html_files if name]}


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("_") or "document"


def build_document_pdf_url(document_id: str, recording_number: str, index: int = 1) -> str:
    clean_document_id = document_id.strip()
    clean_recording_number = recording_number.strip()
    if not clean_document_id or not clean_recording_number:
        raise ValueError("document_id and recording_number are required")
    return (
        f"{BASE_URL}/web/document/servepdf/"
        f"DEGRADED-{clean_document_id}.{index}.pdf/{clean_recording_number}.pdf?index={index}"
    )


def _document_headers(document_id: str, cookie: str) -> dict[str, str]:
    headers = {
        "User-Agent": os.environ.get(
            "COCONINO_USER_AGENT",
            "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        ),
        "Accept": "text/html, */*; q=0.01",
        "Referer": f"{BASE_URL}/web/document/{document_id}?search=DOCSEARCH1213S1",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "keep-alive",
    }
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    return headers


def _extract_address_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    pattern = re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.#'/-]+(?:\s+[A-Za-z0-9.#'/-]+){1,6}\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|CIR|CIRCLE|WAY)\b(?:[^\n,]{0,40})",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text or ""):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" ,")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def fetch_document_detail_fields(document_id: str, cookie: str | None = None, timeout_s: int = 60) -> dict[str, Any]:
    load_env()
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        raise RuntimeError("Coconino session cookie is required for detail fetch")
    url = f"{BASE_URL}/web/document/{document_id}?search=DOCSEARCH1213S1"
    request = Request(url, headers=_document_headers(document_id, effective_cookie), method="GET")
    with urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="ignore")
    pairs = re.findall(r"<strong\s*>\s*([^<:]+):\s*</strong>\s*</div>\s*<div[^>]*>([\s\S]*?)</div>", body, flags=re.IGNORECASE)
    values: dict[str, list[str]] = {}
    for label, raw_value in pairs:
        clean_label = _clean_text(label).lower()
        texts = re.findall(r"<li[^>]*>([\s\S]*?)</li>", raw_value, flags=re.IGNORECASE)
        if texts:
            clean_values = [
                _clean_text(item)
                for item in texts
                if _clean_text(item) and _clean_text(item).lower() != "show more..."
            ]
        else:
            clean_values = [_clean_text(raw_value)] if _clean_text(raw_value) else []
        if clean_values:
            values[clean_label] = clean_values
    property_address = ""
    for key in ("property address", "site address", "address", "situs address"):
        if values.get(key):
            property_address = values[key][0]
            break
    subdivision = ""
    lot = ""
    platted_match = re.search(r"Subdivision:\s*</strong>\s*([^<]+).*?Unit/Lot:\s*</strong>\s*([^<]+)", body, flags=re.IGNORECASE | re.DOTALL)
    if platted_match:
        subdivision = _clean_text(platted_match.group(1))
        lot = _clean_text(platted_match.group(2))
    return {
        "detailUrl": url,
        "propertyAddress": property_address,
        "grantors": values.get("grantor", []),
        "grantees": values.get("grantee", []),
        "subdivision": subdivision,
        "lot": lot,
        "detailHtmlLength": len(body),
    }


def fetch_document_pdf(
    document_id: str,
    recording_number: str,
    index: int = 1,
    cookie: str | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    load_env()
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        raise RuntimeError("Coconino session cookie is required via X-Coconino-Cookie header or COCONINO_COOKIE env var")
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_url = build_document_pdf_url(document_id=document_id, recording_number=recording_number, index=index)
    request = Request(pdf_url, headers=_document_headers(document_id, effective_cookie), method="GET")
    with urlopen(request, timeout=timeout_s) as response:
        body = response.read()
        content_type = response.headers.get("content-type", "")
    if b"<html" in body[:200].lower() or "text/html" in content_type.lower():
        preview = body.decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"Expected PDF but received HTML response: {preview}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{_safe_slug(document_id)}_{_safe_slug(recording_number)}_{index}_{timestamp}.pdf"
    pdf_path = DOCUMENTS_DIR / filename
    pdf_path.write_bytes(body)
    return {
        "documentUrl": pdf_url,
        "pdfPath": str(pdf_path),
        "pdfSize": len(body),
        "contentType": content_type,
    }


def _run_command(command: list[str], timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)


def extract_text_from_pdf(pdf_path: str, timeout_s: int = 60) -> str:
    pdftotext_path = shutil.which("pdftotext")
    if not pdftotext_path:
        return ""
    result = _run_command([pdftotext_path, pdf_path, "-"], timeout_s=timeout_s)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def ocr_pdf(pdf_path: str, timeout_s: int = 240) -> dict[str, Any]:
    pdftoppm_path = shutil.which("pdftoppm")
    tesseract_path = shutil.which("tesseract")
    if not pdftoppm_path or not tesseract_path:
        raise RuntimeError("pdftoppm and tesseract are required for OCR")
    pdfinfo_path = shutil.which("pdfinfo")
    page_count = None
    if pdfinfo_path:
        info_result = _run_command([pdfinfo_path, pdf_path], timeout_s=30)
        if info_result.returncode == 0:
            match = re.search(r"^Pages:\s+(\d+)", info_result.stdout, flags=re.MULTILINE)
            if match:
                page_count = int(match.group(1))
    working_dir = DOCUMENTS_DIR / f"ocr_{_safe_slug(Path(pdf_path).stem)}"
    if working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)
    prefix = working_dir / "page"
    render = _run_command([pdftoppm_path, "-png", pdf_path, str(prefix)], timeout_s=timeout_s)
    if render.returncode != 0:
        raise RuntimeError(render.stderr.strip() or "pdftoppm failed")
    images = sorted(working_dir.glob("page-*.png"))
    if not images:
        raise RuntimeError("No images were rendered from PDF")
    pages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for image_path in images:
        ocr = _run_command([tesseract_path, str(image_path), "stdout"], timeout_s=timeout_s)
        if ocr.returncode != 0:
            raise RuntimeError(ocr.stderr.strip() or f"tesseract failed for {image_path.name}")
        text = ocr.stdout.strip()
        pages.append({"imagePath": str(image_path), "textLength": len(text)})
        if text:
            text_parts.append(text)
    full_text = "\n\n".join(text_parts).strip()
    text_path = working_dir / "ocr_text.txt"
    text_path.write_text(full_text, encoding="utf-8")
    return {
        "ocrText": full_text,
        "ocrTextPath": str(text_path),
        "pageCount": page_count or len(images),
        "pages": pages,
        "ocrMethod": "tesseract",
    }


def analyze_document_text_with_groq(
    document_id: str,
    recording_number: str,
    document_type: str,
    ocr_text: str,
    timeout_s: int = 90,
) -> dict[str, Any]:
    load_env()
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")
    system_prompt = (
        "You analyze county recorder OCR text into strict JSON. "
        "Return a JSON object with keys: summary, parties, property, financials, dates, confidenceNotes. "
        "parties must contain grantors and grantees arrays. property must contain legalDescription and address if visible. "
        "financials must contain amount and loanAmount if visible. dates must contain recordingDate and saleDate if visible. "
        "Do not invent data. Use empty strings or empty arrays when unknown."
    )
    user_payload = {
        "documentId": document_id,
        "recordingNumber": recording_number,
        "documentType": document_type,
        "ocrText": ocr_text[:18000],
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    last_error: Exception | None = None
    for model in DEFAULT_MODEL_CANDIDATES:
        try:
            content = _groq_request(messages, api_key=api_key, model=model, timeout_s=timeout_s)
            data = json.loads(content)
            if isinstance(data, dict):
                data["model"] = model
                return data
        except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Groq document analysis failed: {last_error}")


def fetch_document_ocr_and_analysis(
    document_id: str,
    recording_number: str,
    index: int = 1,
    document_type: str = "",
    cookie: str | None = None,
    use_groq: bool = True,
) -> dict[str, Any]:
    download = fetch_document_pdf(
        document_id=document_id,
        recording_number=recording_number,
        index=index,
        cookie=cookie,
    )
    direct_text = extract_text_from_pdf(download["pdfPath"])
    ocr_result = {
        "ocrText": direct_text,
        "ocrTextPath": "",
        "pageCount": 0,
        "pages": [],
        "ocrMethod": "pdftotext",
    }
    if len(direct_text.strip()) < 80:
        ocr_result = ocr_pdf(download["pdfPath"])
    groq_analysis: dict[str, Any] = {}
    groq_error = ""
    used_groq = False
    if use_groq and ocr_result["ocrText"].strip():
        try:
            groq_analysis = analyze_document_text_with_groq(
                document_id=document_id,
                recording_number=recording_number,
                document_type=document_type,
                ocr_text=ocr_result["ocrText"],
            )
            used_groq = True
        except Exception as exc:
            groq_error = str(exc)
    preview_text = ocr_result["ocrText"][:1500]
    return {
        "documentId": document_id,
        "recordingNumber": recording_number,
        "documentType": document_type,
        **download,
        "ocrMethod": ocr_result["ocrMethod"],
        "ocrTextPath": ocr_result["ocrTextPath"],
        "ocrTextLength": len(ocr_result["ocrText"]),
        "ocrTextPreview": preview_text,
        "pageCount": ocr_result["pageCount"],
        "ocrPages": ocr_result["pages"],
        "requestedGroq": use_groq,
        "usedGroq": used_groq,
        "groqError": groq_error,
        "groqAnalysis": groq_analysis,
        "addressCandidates": _extract_address_candidates(ocr_result["ocrText"]),
    }


def resolve_html_file(html_file: str) -> Path:
    candidate = (OUTPUT_DIR / html_file).resolve() if not os.path.isabs(html_file) else Path(html_file).resolve()
    allowed_root = ROOT_DIR.resolve()
    if allowed_root not in candidate.parents and candidate != allowed_root:
        raise ValueError("html_file must stay inside conino directory")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"HTML file not found: {html_file}")
    return candidate


def _clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_label(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", label or "").strip().lower()


def _parse_summary(html_text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    match = re.search(
        r"Showing\s+page\s+(\d+)\s+of\s+(\d+)\s+for\s+(\d+)\s+Total Results",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        summary["page"] = int(match.group(1))
        summary["pageCount"] = int(match.group(2))
        summary["totalResults"] = int(match.group(3))
    filter_match = re.search(
        r"<div class=\"selfServiceSearchResultHeaderLeft\">\s*Recordings\s+(.*?)</div>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if filter_match:
        summary["filterSummary"] = _clean_text(filter_match.group(1))
    return summary


def _row_blocks(html_text: str) -> list[str]:
    pattern = re.compile(
        r"(<li class=\"ss-search-row\"[\s\S]*?<p class=\"selfServiceSearchFullResult selfServiceSearchResultNavigation\">[\s\S]*?</div>\s*</li>)",
        flags=re.IGNORECASE,
    )
    return [match.group(1) for match in pattern.finditer(html_text)]


def _extract_column_values(block: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    ul_pattern = re.compile(
        r"<ul class=\"selfServiceSearchResultColumn[^\"]*\">([\s\S]*?)</ul>",
        flags=re.IGNORECASE,
    )
    li_pattern = re.compile(r"<li[^>]*>([\s\S]*?)</li>", flags=re.IGNORECASE)
    bold_pattern = re.compile(r"<b>([\s\S]*?)</b>", flags=re.IGNORECASE)
    for ul_match in ul_pattern.finditer(block):
        ul_body = ul_match.group(1)
        li_matches = li_pattern.findall(ul_body)
        if not li_matches:
            continue
        label = _normalize_label(_clean_text(li_matches[0]))
        values = [_clean_text(value) for value in bold_pattern.findall(ul_body)]
        if label:
            existing = result.setdefault(label, [])
            existing.extend(value for value in values if value and value not in existing)
    return result


def parse_search_results_html(html_text: str, source_file: str) -> dict[str, Any]:
    rows: list[ExtractedRecord] = []
    for block in _row_blocks(html_text):
        document_id_match = re.search(r'data-documentid="([^"]+)"', block, flags=re.IGNORECASE)
        href_match = re.search(r'data-href="([^"]+)"', block, flags=re.IGNORECASE)
        header_match = re.search(r"<h1>([\s\S]*?)</h1>", block, flags=re.IGNORECASE)
        header_text = _clean_text(header_match.group(1) if header_match else "")
        header_parts = [part.strip() for part in re.split(r"\s*·\s*", header_text) if part.strip()]
        columns = _extract_column_values(block)
        document_id = document_id_match.group(1) if document_id_match else ""
        if len(header_parts) < 3 and header_text:
            header_parts = [part.strip() for part in re.split(r"\s*[•·]\s*", header_text) if part.strip()]
        recording_number = header_parts[0] if header_parts else header_text
        document_type = header_parts[1] if len(header_parts) > 1 else ""
        recording_date = header_parts[2] if len(header_parts) > 2 else ""
        detail_path = href_match.group(1) if href_match else ""
        rows.append(
            ExtractedRecord(
                document_id=document_id,
                recording_number=recording_number,
                document_type=document_type,
                recording_date=recording_date,
                grantors=columns.get("grantor", []),
                grantees=columns.get("grantee", []),
                legal_descriptions=columns.get("legal", []),
                property_address="",
                detail_url=f"{BASE_URL}{detail_path}" if detail_path.startswith("/") else detail_path,
                source_file=source_file,
                raw_html=block,
            )
        )
    return {
        "summary": _parse_summary(html_text),
        "records": [row.as_dict() for row in rows],
        "rawRecords": rows,
    }


def _chunk_records(records: list[ExtractedRecord], batch_size: int) -> list[list[ExtractedRecord]]:
    return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]


def _groq_request(messages: list[dict[str, str]], api_key: str, model: str, timeout_s: int) -> str:
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    request = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body)
    return data["choices"][0]["message"]["content"]


def enrich_with_groq(records: list[ExtractedRecord], batch_size: int = 5, timeout_s: int = 60) -> list[dict[str, Any]]:
    load_env()
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")
    normalized: list[dict[str, Any]] = []
    system_prompt = (
        "You extract Arizona recorder search result rows into strict JSON. "
        "Return one object per input row in the same order. "
        "Each object must have keys: documentId, recordingNumber, documentType, recordingDate, "
        "grantors, grantees, legalDescriptions, detailUrl. grantors/grantees/legalDescriptions must be arrays of strings. "
        "Do not invent values."
    )
    for batch in _chunk_records(records, max(1, batch_size)):
        user_payload = {
            "rows": [
                {
                    "preparsed": row.as_dict(),
                    "htmlSnippet": row.raw_html[:6000],
                }
                for row in batch
            ]
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        last_error: Exception | None = None
        content = ""
        for model in DEFAULT_MODEL_CANDIDATES:
            try:
                content = _groq_request(messages, api_key=api_key, model=model, timeout_s=timeout_s)
                last_error = None
                break
            except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
        if last_error is not None and not content:
            raise RuntimeError(f"Groq extraction failed: {last_error}")
        parsed = json.loads(content)
        rows = parsed.get("rows") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Groq returned unexpected JSON shape")
        for index, item in enumerate(rows):
            base = batch[index].as_dict()
            normalized.append(
                {
                    "documentId": str(item.get("documentId") or base["documentId"]),
                    "recordingNumber": str(item.get("recordingNumber") or base["recordingNumber"]),
                    "documentType": str(item.get("documentType") or base["documentType"]),
                    "recordingDate": str(item.get("recordingDate") or base["recordingDate"]),
                    "grantors": _string_list(item.get("grantors"), base["grantors"]),
                    "grantees": _string_list(item.get("grantees"), base["grantees"]),
                    "legalDescriptions": _string_list(item.get("legalDescriptions"), base["legalDescriptions"]),
                    "detailUrl": str(item.get("detailUrl") or base["detailUrl"]),
                    "sourceFile": base["sourceFile"],
                }
            )
    return normalized


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if cleaned:
            return cleaned
    return fallback


def export_csv(records: list[dict[str, Any]], csv_name: str | None = None) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    filename = csv_name.strip() if csv_name else f"coconino_results_{timestamp}.csv"
    if not filename.lower().endswith(".csv"):
        filename = f"{filename}.csv"
    path = (OUTPUT_DIR / filename).resolve()
    if OUTPUT_DIR.resolve() not in path.parents and path != OUTPUT_DIR.resolve():
        raise ValueError("csv_name must stay inside conino/output")
    fieldnames = [
        "documentId",
        "recordingNumber",
        "documentType",
        "recordingDate",
        "grantors",
        "grantees",
        "legalDescriptions",
        "propertyAddress",
        "detailUrl",
        "sourceFile",
        "documentUrl",
        "ocrMethod",
        "ocrTextPreview",
        "ocrTextPath",
        "usedGroq",
        "groqError",
        "documentAnalysisError",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            document_analysis = record.get("documentAnalysis") or {}
            writer.writerow(
                {
                    **record,
                    "grantors": " | ".join(record.get("grantors", [])),
                    "grantees": " | ".join(record.get("grantees", [])),
                    "legalDescriptions": " | ".join(record.get("legalDescriptions", [])),
                    "propertyAddress": record.get("propertyAddress", ""),
                    "documentUrl": document_analysis.get("documentUrl", ""),
                    "ocrMethod": document_analysis.get("ocrMethod", ""),
                    "ocrTextPreview": (document_analysis.get("ocrTextPreview", "") or "")[:500],
                    "ocrTextPath": document_analysis.get("ocrTextPath", ""),
                    "usedGroq": document_analysis.get("usedGroq", record.get("usedGroq", False)),
                    "groqError": document_analysis.get("groqError", record.get("groqError", "")),
                    "documentAnalysisError": record.get("documentAnalysisError", ""),
                }
            )
    return path


def fetch_session_results_pages(cookie: str, page_limit: int | None = None, save_html: bool = True) -> dict[str, Any]:
    opener = build_opener(HTTPCookieProcessor())
    all_records: list[dict[str, Any]] = []
    html_files: list[str] = []
    summary: dict[str, Any] = {}
    current_page = 1
    while True:
        request = Request(f"{SEARCH_RESULTS_URL}?page={current_page}", headers=_search_headers(cookie, ajax=True), method="GET")
        body = opener.open(request, timeout=60).read().decode("utf-8", errors="ignore")
        source_name = _save_live_html(f"session_results_page_{current_page}", body) if save_html else f"session_page_{current_page}.html"
        if save_html:
            html_files.append(source_name)
        parsed = parse_search_results_html(body, source_file=source_name)
        page_summary = parsed.get("summary", {})
        summary = page_summary or summary
        records = parsed.get("records", [])
        if not records:
            break
        all_records.extend(records)
        total_pages = int(page_summary.get("pageCount") or current_page)
        if current_page >= total_pages:
            break
        if page_limit is not None and current_page >= page_limit:
            break
        current_page += 1
    return {"summary": summary, "records": all_records, "htmlFiles": html_files}


def enrich_records_with_detail_fields(records: list[dict[str, Any]], cookie: str | None = None, max_records: int | None = None) -> list[dict[str, Any]]:
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        return records
    enriched: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        updated = dict(record)
        if max_records is None or index < max_records:
            try:
                detail = fetch_document_detail_fields(str(record.get("documentId", "")), cookie=effective_cookie)
                if detail.get("grantors"):
                    updated["grantors"] = detail["grantors"]
                if detail.get("grantees"):
                    updated["grantees"] = detail["grantees"]
                if detail.get("propertyAddress"):
                    updated["propertyAddress"] = detail["propertyAddress"]
                if not updated.get("legalDescriptions") and detail.get("subdivision"):
                    legal = detail["subdivision"]
                    if detail.get("lot"):
                        legal = f"Subdivision {legal} Lot {detail['lot']}"
                    updated["legalDescriptions"] = [legal]
                if not updated.get("propertyAddress") and updated.get("legalDescriptions"):
                    updated["propertyAddress"] = updated["legalDescriptions"][0]
            except Exception as exc:
                updated["detailError"] = str(exc)
        enriched.append(updated)
    return enriched


def search_to_csv(
    start_date: str | None = None,
    end_date: str | None = None,
    document_types: list[str] | None = None,
    use_groq: bool = True,
    csv_name: str | None = None,
    include_document_analysis: bool = False,
    document_limit: int = 0,
    document_index: int = 1,
    page_limit: int | None = None,
    cookie: str | None = None,
    save_html: bool = True,
    use_current_session_results: bool = False,
) -> dict[str, Any]:
    load_env()
    default_start, default_end = default_last_three_month_range()
    effective_start = start_date or default_start
    effective_end = end_date or default_end
    normalized_document_types = _normalize_document_types(document_types)
    live_error = ""
    data_source = "live-search"
    warnings: list[str] = []
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if use_current_session_results:
        session_results = fetch_session_results_pages(effective_cookie, page_limit=page_limit, save_html=save_html)
        records = _dedupe_records(list(session_results.get("records", [])))
        records = enrich_records_with_detail_fields(records, cookie=cookie, max_records=None)
        csv_path = export_csv(records, csv_name=csv_name)
        return _build_search_response(
            start_date=_normalize_date(effective_start),
            end_date=_normalize_date(effective_end),
            document_types=normalized_document_types,
            records=records,
            summary={**session_results.get("summary", {}), "mode": "current-session-results"},
            html_files=session_results.get("htmlFiles", []),
            csv_path=csv_path,
            data_source="current-session-results",
            live_error="",
            include_document_analysis=include_document_analysis,
            document_limit=document_limit,
            use_groq=use_groq,
            used_fallback=False,
        )
    try:
        live = run_live_search(
            start_date=effective_start,
            end_date=effective_end,
            document_types=document_types,
            page_limit=page_limit,
            cookie=cookie,
            save_html=save_html,
        )
        records = _dedupe_records(list(live.get("records", [])))
        summary = live.get("summary", {})
        html_files = live.get("htmlFiles", [])
    except Exception as exc:
        live_error = str(exc)
        data_source = "saved-html-fallback"
        warnings.append(f"Live county search failed; using fallback data. {live_error}")
        try:
            if not effective_cookie:
                raise RuntimeError("No session cookie available for pagination fallback")
            session_results = fetch_session_results_pages(effective_cookie, page_limit=page_limit, save_html=save_html)
            session_records = _dedupe_records(
                _filter_records(session_results.get("records", []), effective_start, effective_end, normalized_document_types)
            )
            if not session_records:
                raise RuntimeError("Session pagination fallback returned no matching records")
            records = session_records
            summary = {
                **session_results.get("summary", {}),
                "requestedStartDate": _normalize_date(effective_start),
                "requestedEndDate": _normalize_date(effective_end),
                "requestedDocumentTypes": normalized_document_types,
            }
            html_files = session_results.get("htmlFiles", [])
            data_source = "session-pagination-fallback"
        except Exception:
            try:
                fallback_path = latest_saved_results_html()
                parsed = parse_search_results_html(fallback_path.read_text(encoding="utf-8", errors="ignore"), fallback_path.name)
                records = _dedupe_records(_filter_records(parsed.get("records", []), effective_start, effective_end, normalized_document_types))
                summary = {
                    **parsed.get("summary", {}),
                    "requestedStartDate": _normalize_date(effective_start),
                    "requestedEndDate": _normalize_date(effective_end),
                    "requestedDocumentTypes": normalized_document_types,
                }
                html_files = [fallback_path.name]
                data_source = "saved-html-fallback"
            except FileNotFoundError:
                records = []
                summary = {
                    "requestedStartDate": _normalize_date(effective_start),
                    "requestedEndDate": _normalize_date(effective_end),
                    "requestedDocumentTypes": normalized_document_types,
                }
                html_files = []
                data_source = "no-fallback-data"
                warnings.append("No saved search results HTML is available in conino/output.")
    if include_document_analysis and records:
        effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
        for index, record in enumerate(records):
            if index >= max(0, document_limit):
                break
            try:
                record["documentAnalysis"] = fetch_document_ocr_and_analysis(
                    document_id=str(record.get("documentId", "")),
                    recording_number=str(record.get("recordingNumber", "")),
                    index=document_index,
                    document_type=str(record.get("documentType", "")),
                    cookie=effective_cookie,
                    use_groq=use_groq,
                )
                if not record.get("propertyAddress"):
                    candidates = record["documentAnalysis"].get("addressCandidates") or []
                    if candidates:
                        record["propertyAddress"] = candidates[0]
            except Exception as exc:
                record["documentAnalysisError"] = str(exc)
    records = enrich_records_with_detail_fields(records, cookie=cookie, max_records=None)
    records = _dedupe_records(records)
    csv_path = export_csv(records, csv_name=csv_name)
    return _build_search_response(
        start_date=_normalize_date(effective_start),
        end_date=_normalize_date(effective_end),
        document_types=normalized_document_types,
        records=records,
        summary=summary,
        html_files=html_files,
        csv_path=csv_path,
        data_source=data_source,
        live_error=live_error,
        include_document_analysis=include_document_analysis,
        document_limit=document_limit,
        use_groq=use_groq,
        used_fallback=data_source != "live-search",
        warnings=warnings,
    )


def extract_to_csv(
    html_file: str,
    limit: int | None = None,
    offset: int = 0,
    use_groq: bool = True,
    csv_name: str | None = None,
    document_types: list[str] | None = None,
) -> dict[str, Any]:
    load_env()
    path = resolve_html_file(html_file)
    html_text = path.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_search_results_html(html_text, source_file=path.name)
    raw_records: list[ExtractedRecord] = parsed.pop("rawRecords")
    if document_types:
        requested = {item.strip().lower() for item in document_types if item.strip()}
        raw_records = [row for row in raw_records if row.document_type.strip().lower() in requested]
    if offset > 0:
        raw_records = raw_records[offset:]
    if limit is not None and limit >= 0:
        raw_records = raw_records[:limit]
    groq_error = ""
    groq_used = False
    records = [row.as_dict() for row in raw_records]
    if use_groq and raw_records:
        try:
            records = enrich_with_groq(raw_records)
            groq_used = True
        except Exception as exc:
            groq_error = str(exc)
    csv_path = export_csv(records, csv_name=csv_name)
    return {
        "ok": True,
        "summary": parsed.get("summary", {}),
        "htmlFile": path.name,
        "recordCount": len(records),
        "csvFile": csv_path.name,
        "csvPath": str(csv_path),
        "requestedGroq": use_groq,
        "usedGroq": groq_used,
        "groqError": groq_error,
        "records": records,
    }
