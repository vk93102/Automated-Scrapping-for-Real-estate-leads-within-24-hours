from __future__ import annotations

from pathlib import Path
from typing import Optional

import requests

from .http_client import RetryConfig, with_retry


def fetch_pdf_bytes(
    session: requests.Session,
    recording_number: str,
    *,
    timeout_s: float = 60.0,
    proxies: Optional[dict[str, str]] = None,
    retry: Optional[RetryConfig] = None,
) -> bytes:
    """Fetch the PDF bytes for a recording number without saving to disk."""

    url = f"https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf/{recording_number}.pdf"
    cfg = retry or RetryConfig(attempts=3, base_sleep_s=2.0, max_sleep_s=20.0)

    def _do() -> requests.Response:
        return session.get(url, timeout=timeout_s, proxies=proxies)

    resp = with_retry(_do, cfg=cfg)
    resp.raise_for_status()
    if "application/pdf" not in (resp.headers.get("content-type") or "").lower():
        raise RuntimeError(
            f"unexpected content-type for {recording_number}: {resp.headers.get('content-type')}"
        )
    return resp.content


def download_pdf(
    session: requests.Session,
    recording_number: str,
    *,
    out_dir: str = "downloads/documents",
    timeout_s: float = 60.0,
    proxies: Optional[dict[str, str]] = None,
    retry: Optional[RetryConfig] = None,
) -> Path:
    url = f"https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf/{recording_number}.pdf"
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    out_path = p / f"{recording_number}.pdf"

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    cfg = retry or RetryConfig(attempts=3, base_sleep_s=2.0, max_sleep_s=20.0)

    def _do() -> requests.Response:
        return session.get(url, timeout=timeout_s, proxies=proxies)

    resp = with_retry(_do, cfg=cfg)
    resp.raise_for_status()
    if "application/pdf" not in (resp.headers.get("content-type") or "").lower():
        # Some blocks return HTML; don't write it as a PDF.
        raise RuntimeError(f"unexpected content-type for {recording_number}: {resp.headers.get('content-type')}")

    out_path.write_bytes(resp.content)
    return out_path
