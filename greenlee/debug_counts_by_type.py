from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from lapaz.extractor import (
    _goto_document_search,
    _execute_search_for_doc_type,
    _collect_result_pages,
    parse_results_html,
)

DOC_TYPES = [
    "NOTICE OF DEFAULT",
    "NOTICE OF TRUSTEE SALE",
    "LIS PENDENS",
    "DEED IN LIEU",
    "TREASURERS DEED",
    "NOTICE OF REINSTATEMENT",
]


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()

        _goto_document_search(page, verbose=True)

        for dt in DOC_TYPES:
            print(f"\n=== {dt} ===")
            ok = _execute_search_for_doc_type(
                page,
                start_date="3/10/2026",
                end_date="3/17/2026",
                doc_type=dt,
                verbose=True,
            )
            print("execute ok:", ok, "url:", page.url)
            if not ok:
                _goto_document_search(page, verbose=False)
                continue
            html_pages = _collect_result_pages(page, max_pages=0, verbose=False)
            print("pages:", len(html_pages))
            total = 0
            for i, html in enumerate(html_pages, 1):
                recs = parse_results_html(html, source_doc_type=dt)
                print(f"  page{i} recs:", len(recs))
                if recs:
                    for r in recs[:3]:
                        print("   ", r.get("documentId"), r.get("documentType"), r.get("recordingDate"))
                total += len(recs)
            print("total parsed:", total)
            _goto_document_search(page, verbose=False)

        browser.close()


if __name__ == "__main__":
    main()
