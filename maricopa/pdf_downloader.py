from __future__ import annotations

from pathlib import Path
from typing import Optional

import requests

from .http_client import RetryConfig, with_retry

# PDF URL priority order:
#   1. Legacy unofficial docs (fastest, no auth)
#   2. Public API preview endpoint (fallback)
_LEGACY_URL = "https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf/{rn}.pdf"
_PREVIEW_URL = "https://publicapi.recorder.maricopa.gov/preview/pdf?recordingNumber={rn}&suffix="


def preview_pdf_url(recording_number: str) -> str:
    """Return a stable, publicly accessible PDF URL for a recording number."""
    rn = str(recording_number).strip()
    return _PREVIEW_URL.format(rn=rn)


def legacy_pdf_url(recording_number: str) -> str:
    rn = str(recording_number).strip()
    return _LEGACY_URL.format(rn=rn)


def _try_get_pdf(
    session: requests.Session,
    url: str,
    *,
    timeout_s: float,
    proxies: Optional[dict],
    cfg: RetryConfig,
) -> bytes:
    """GET a URL and return bytes; raises if content-type is not PDF."""
    def _do() -> requests.Response:
        return session.get(url, timeout=timeout_s, proxies=proxies)

    resp = with_retry(_do, cfg=cfg)
    resp.raise_for_status()
    ct = (resp.headers.get("content-type") or "").lower()
    if "application/pdf" not in ct and "octet-stream" not in ct:
        raise RuntimeError(f"unexpected content-type '{ct}' from {url}")
    return resp.content


def fetch_pdf_bytes(
    session: requests.Session,
    recording_number: str,
    *,
    timeout_s: float = 60.0,
    proxies: Optional[dict[str, str]] = None,
    retry: Optional[RetryConfig] = None,
) -> bytes:
    """
    Fetch PDF bytes for a recording number.

    Tries the legacy URL first; falls back to the public API preview endpoint
    if the legacy URL 404s or returns unexpected content.
    """
    cfg = retry or RetryConfig(attempts=3, base_sleep_s=2.0, max_sleep_s=20.0)
    rn = str(recording_number).strip()

    legacy_url  = _LEGACY_URL.format(rn=rn)
    preview_url = _PREVIEW_URL.format(rn=rn)

    for label, url in [("legacy", legacy_url), ("preview", preview_url)]:
        try:
            return _try_get_pdf(session, url, timeout_s=timeout_s, proxies=proxies, cfg=cfg)
        except Exception as exc:
            last_exc = exc
            import logging
            logging.getLogger(__name__).warning(
                "PDF fetch [%s] failed for %s: %s — %s",
                label, rn, url, exc,
            )

    raise RuntimeError(f"Could not fetch PDF for {rn} from any URL: {last_exc}")


def download_pdf(
    session: requests.Session,
    recording_number: str,
    *,
    out_dir: str = "downloads/documents",
    timeout_s: float = 60.0,
    proxies: Optional[dict[str, str]] = None,
    retry: Optional[RetryConfig] = None,
) -> Path:
    """Download and save a PDF to disk; returns the saved path."""
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    out_path = p / f"{recording_number}.pdf"

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    data = fetch_pdf_bytes(
        session, recording_number,
        timeout_s=timeout_s, proxies=proxies, retry=retry,
    )
    out_path.write_bytes(data)
    return out_path

