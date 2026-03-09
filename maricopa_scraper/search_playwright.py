from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


_REC_RE = re.compile(r"\b\d{11}\b")
@dataclass(frozen=True)
class SearchParams:
    document_code: str
    begin_date: date
    end_date: date


def build_search_url(params: SearchParams) -> str:
    # Include documentTypeSelector=code to match the demo UI query string
    return (
        "https://recorder.maricopa.gov/recording/document-search-results.html"
        f"?documentTypeSelector=code"
        f"&documentCode=NS"
        f"&beginDate={params.begin_date.isoformat()}"
        f"&endDate={params.end_date.isoformat()}"
    )


def scrape_recording_numbers_with_playwright(
    *,
    params: SearchParams,
    storage_state_path: str = "storage_state.json",
    headful: bool = False,
    proxy_server: Optional[str] = None,
    browser_executable: Optional[str] = None,
    timeout_ms: int = 60_000,
) -> list[str]:
    """Scrape recording numbers from the search results page.

    The search UI is protected by a Cloudflare challenge (403 to plain HTTP).
    Using Playwright allows JavaScript execution and session cookies.

    Recommended flow:
    1) Run once with `--headful` to solve the challenge.
    2) The script saves `storage_state.json` for cron/headless runs.
    """

    from playwright.sync_api import sync_playwright

    url = build_search_url(params)

    storage_path = Path(storage_state_path)
    storage_state: Optional[str] = str(storage_path) if storage_path.exists() else None

    with sync_playwright() as p:
        launch_args: dict = {"headless": (not headful)}
        if proxy_server:
            launch_args["proxy"] = {"server": proxy_server}
        if browser_executable:
            # Allow launching a specific browser executable (Brave, Chromium, etc.)
            launch_args["executable_path"] = browser_executable

        # Launch browser and create a slightly more realistic context to
        # reduce Cloudflare/anti-bot detection. If `executable_path` is
        # provided, Playwright will use that binary (e.g. Brave).
        browser = p.chromium.launch(**launch_args)
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        )
        context = browser.new_context(
            storage_state=storage_state,
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            java_script_enabled=True,
        )

        # Reduce automation detection by overriding navigator.webdriver early
        try:
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception:
            pass

        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        # Set common headers
        try:
            page.set_extra_http_headers({
                "accept-language": "en-US,en;q=0.9",
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "referer": "https://recorder.maricopa.gov/",
            })
        except Exception:
            pass
        # NOTE: this site often never reaches "networkidle" due to background requests.
        page.goto(url, wait_until="domcontentloaded")

        # Best-effort: wait a bit for JS-rendered results (and/or Cloudflare challenge)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        page.wait_for_timeout(12_000)
        html = page.content()

        # Save updated cookies for next run
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_path))
        finally:
            context.close()
            browser.close()

    recs = sorted(set(_REC_RE.findall(html)))
    return recs
