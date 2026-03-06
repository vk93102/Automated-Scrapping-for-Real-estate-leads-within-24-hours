from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_REC_RE = re.compile(r"\b\d{11}\b")

_BLOCK_PHRASES = [
    "security check",
    "just a moment",
    "captcha",
    "enable javascript",
    "please wait",
    "checking your browser",
    "ddos-guard",
]


@dataclass(frozen=True)
class SearchParams:
    document_code: str
    begin_date: date
    end_date: date


def build_search_url(params: SearchParams) -> str:
    """Build the Maricopa search results URL matching the demo UI query string."""
    return (
        "https://recorder.maricopa.gov/recording/document-search-results.html"
        "?documentTypeSelector=code"
        f"&documentCode={params.document_code}"
        f"&beginDate={params.begin_date.isoformat()}"
        f"&endDate={params.end_date.isoformat()}"
    )


def _is_blocked(html: str) -> bool:
    """Return True if the page content is a security / CAPTCHA interstitial."""
    low = html.lower()
    return any(phrase in low for phrase in _BLOCK_PHRASES)


def _playwright_proxy_cfg(proxy_url: str) -> dict:
    """Convert http://user:pass@host:port to a Playwright proxy dict."""
    parsed = urlparse(proxy_url)
    cfg: dict = {"server": f"http://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        cfg["username"] = parsed.username
    if parsed.password:
        cfg["password"] = parsed.password
    return cfg


def _try_scrape(
    *,
    url: str,
    proxy_url: Optional[str],
    storage_state: Optional[str],
    headful: bool,
    browser_executable: Optional[str],
    timeout_ms: int,
    storage_path: Path,
) -> Optional[list]:
    """One Playwright attempt with a given proxy.  Returns recording numbers or None if blocked."""
    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        launch_args: dict = {"headless": not headful}
        if proxy_url:
            launch_args["proxy"] = _playwright_proxy_cfg(proxy_url)
        if browser_executable:
            launch_args["executable_path"] = browser_executable

        browser = p.chromium.launch(**launch_args)
        try:
            context = browser.new_context(
                storage_state=storage_state,
                user_agent=ua,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                timezone_id="America/Phoenix",
                java_script_enabled=True,
            )
            try:
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    "window.chrome = {runtime: {}};"
                )
            except Exception:
                pass

            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            try:
                page.set_extra_http_headers({
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/avif,image/webp,*/*;q=0.8"
                    ),
                    "Referer": "https://recorder.maricopa.gov/",
                    "DNT": "1",
                })
            except Exception:
                pass

            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            page.wait_for_timeout(8_000)

            html = page.content()

            if _is_blocked(html):
                logger.warning(
                    "Proxy %s hit security check — trying next proxy",
                    proxy_url or "direct",
                )
                try:
                    Path("output").mkdir(exist_ok=True)
                    page.screenshot(path="output/playwright_blocked.png", full_page=True)
                except Exception:
                    pass
                return None

            try:
                storage_path.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(storage_path))
            except Exception:
                pass

            recs = sorted(set(_REC_RE.findall(html)))
            logger.info(
                "Proxy %s succeeded — found %d recording numbers",
                proxy_url or "direct",
                len(recs),
            )
            return recs

        finally:
            try:
                browser.close()
            except Exception:
                pass


def scrape_recording_numbers_with_playwright(
    *,
    params: SearchParams,
    storage_state_path: str = "storage_state.json",
    headful: bool = False,
    proxy_server: Optional[str] = None,
    proxy_list: Optional[list] = None,
    browser_executable: Optional[str] = None,
    timeout_ms: int = 60_000,
) -> list:
    """
    Scrape recording numbers from the Maricopa Recorder search page.

    Proxy rotation: shuffles proxy_list and tries each in order.
    Falls back to proxy_server, then direct.
    Saves storage_state.json on first success for future headless runs.
    """
    url = build_search_url(params)
    logger.info("Playwright target URL: %s", url)

    storage_path = Path(storage_state_path)
    storage_state: Optional[str] = str(storage_path) if storage_path.exists() else None

    candidates: list = []
    if proxy_list:
        shuffled = list(proxy_list)
        random.shuffle(shuffled)
        candidates.extend(shuffled)
    if proxy_server and proxy_server not in candidates:
        candidates.append(proxy_server)
    if not candidates:
        candidates.append(None)  # direct

    for attempt, proxy_url in enumerate(candidates, start=1):
        logger.info(
            "Playwright attempt %d/%d — proxy: %s",
            attempt, len(candidates), proxy_url or "direct",
        )
        try:
            result = _try_scrape(
                url=url,
                proxy_url=proxy_url,
                storage_state=storage_state,
                headful=headful,
                browser_executable=browser_executable,
                timeout_ms=timeout_ms,
                storage_path=storage_path,
            )
            if result is not None:
                return result
        except Exception as exc:
            logger.warning(
                "Playwright attempt %d failed (%s): %s",
                attempt, proxy_url or "direct", exc,
            )

    logger.error(
        "All %d proxy attempts exhausted — no recording numbers found. "
        "Run with --headful to solve the CAPTCHA interactively and save storage_state.json.",
        len(candidates),
    )
    return []
