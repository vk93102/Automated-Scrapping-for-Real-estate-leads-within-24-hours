from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import requests

from .http_client import RetryConfig, with_retry


@dataclass(frozen=True)
class DocumentMetadata:
    recording_number: str
    recording_date: Optional[str]
    document_codes: list[str]
    names: list[str]
    page_amount: Optional[int]
    restricted: bool = False


def search_recording_numbers(
    session: requests.Session,
    *,
    document_codes: Optional[list[str]] = None,
    begin_date: date,
    end_date: date,
    page_size: int = 200,
    max_results: Optional[int] = None,
    timeout_s: float = 30.0,
    proxies: Optional[dict[str, str]] = None,
    retry: Optional[RetryConfig] = None,
) -> list[str]:
    """Search for recording numbers via the public API.

    Uses: GET https://publicapi.recorder.maricopa.gov/documents/search
    This avoids the Cloudflare Turnstile challenge on the HTML search page.
    """

    url = "https://publicapi.recorder.maricopa.gov/documents/search"
    cfg = retry or RetryConfig()

    codes = [c.strip() for c in (document_codes or []) if (c or "").strip()]
    page_number = 1
    out: list[str] = []
    seen: set[str] = set()
    total_results: Optional[int] = None

    while True:
        params: dict[str, Any] = {
            "beginDate": begin_date.isoformat(),
            "endDate": end_date.isoformat(),
            "pageNumber": page_number,
            "pageSize": int(page_size),
        }
        if codes:
            # Requests will encode list values as repeated query params.
            params["documentCode"] = codes

        def _do() -> requests.Response:
            return session.get(url, params=params, timeout=timeout_s, proxies=proxies)

        resp = with_retry(_do, cfg=cfg)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json() or {}

        if total_results is None:
            try:
                total_results = int(data.get("totalResults"))
            except Exception:
                total_results = None

        rows = data.get("searchResults") or []
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            rn = row.get("recordingNumber")
            if rn is None:
                continue
            rn_s = str(rn).strip()
            if not rn_s or rn_s in seen:
                continue
            seen.add(rn_s)
            out.append(rn_s)
            if max_results is not None and len(out) >= int(max_results):
                return out

        if total_results is not None and len(seen) >= total_results:
            break
        page_number += 1

        # Safety stop to avoid infinite loops if API returns inconsistent totals.
        if page_number > 500:
            break

    return out


def fetch_metadata(
    session: requests.Session,
    recording_number: str,
    *,
    timeout_s: float = 30.0,
    proxies: Optional[dict[str, str]] = None,
    retry: Optional[RetryConfig] = None,
) -> DocumentMetadata:
    url = f"https://publicapi.recorder.maricopa.gov/documents/{recording_number}"
    cfg = retry or RetryConfig()

    def _do() -> requests.Response:
        return session.get(url, timeout=timeout_s, proxies=proxies)

    resp = with_retry(_do, cfg=cfg)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()

    # The API returns lists and strings; normalize conservatively.
    names = [str(x).strip() for x in (data.get("names") or []) if str(x).strip()]
    doc_codes = [str(x).strip() for x in (data.get("documentCodes") or []) if str(x).strip()]
    page_amount = data.get("pageAmount")
    try:
        page_amount_int = int(page_amount) if page_amount is not None else None
    except Exception:
        page_amount_int = None

    restricted = bool(data.get("restricted", False))

    return DocumentMetadata(
        recording_number=str(data.get("recordingNumber") or recording_number),
        recording_date=(str(data.get("recordingDate")).strip() if data.get("recordingDate") else None),
        document_codes=doc_codes,
        names=names,
        page_amount=page_amount_int,
        restricted=restricted,
    )
