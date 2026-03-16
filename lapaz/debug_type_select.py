#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from lapaz.extractor import _goto_document_search, _execute_search_for_doc_type


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()

        _goto_document_search(page, verbose=True)
        print("URL after goto:", page.url)

        # inspect type options before
        type_sel = "select[id*='cboDocumentType']"
        if page.locator(type_sel).count() > 0:
            opts_before = page.locator(type_sel).first.evaluate(
                "el => Array.from(el.options).map(o => (o.text || '').trim())"
            )
            print("Type options before search setup:", opts_before[:10])

        ok = _execute_search_for_doc_type(
            page,
            start_date="3/9/2026",
            end_date="3/16/2026",
            doc_type="NOTICE OF TRUSTEE SALE",
            verbose=True,
        )
        print("execute_search_for_doc_type returned:", ok)
        print("URL after execute:", page.url)

        if page.locator(type_sel).count() > 0:
            opts_after = page.locator(type_sel).first.evaluate(
                "el => Array.from(el.options).map(o => (o.text || '').trim())"
            )
            print("Type options after search setup:", opts_after[:20])

        print("Page title:", page.title())
        browser.close()


if __name__ == "__main__":
    main()
