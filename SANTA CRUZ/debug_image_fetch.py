from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor import _goto_document_search, _cookie_header_from_cookies, _make_session, fetch_detail, discover_image_urls
from playwright.sync_api import sync_playwright

DK = "736875"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    _goto_document_search(page, verbose=True)
    cookie_header = _cookie_header_from_cookies(context.cookies())
    browser.close()

session = _make_session(cookie_header)
detail = fetch_detail(DK, session)
print('detail imageUrls:', detail.get('imageUrls'))
urls = discover_image_urls(DK, session, detail.get('imageUrls', []), max_probe_pages=6)
print('discovered:', urls)
for u in [f'https://www.thecountyrecorder.com/ImageHandler.ashx?DK={DK}&PN={i}' for i in range(1,4)]:
    try:
        head = session.head(u, timeout=15, allow_redirects=True)
        print('HEAD', u, head.status_code, head.headers.get('Content-Type'), head.headers.get('Content-Length'))
    except Exception as e:
        print('HEAD ERR', u, e)
    try:
        rr = session.get(u, timeout=15, allow_redirects=True)
        print('GET ', u, rr.status_code, rr.headers.get('Content-Type'), len(rr.content))
    except Exception as e:
        print('GET ERR', u, e)
