from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from greenlee.extractor import (
    _goto_document_search,
    _execute_search_for_doc_type,
    _collect_result_pages,
    parse_results_html,
)

DOC_TYPES = [
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
        all_ids = set()

        for dt in DOC_TYPES:
            print(f"\n=== {dt} ===")
            ok = _execute_search_for_doc_type(
                page,
                start_date="3/1/2026",
                end_date="3/17/2026",
                doc_type=dt,
                verbose=True,
            )
            if not ok:
                print("  execute: not ok")
                _goto_document_search(page, verbose=False)
                continue

            html_pages = _collect_result_pages(page, max_pages=0, verbose=False)
            print("  pages:", len(html_pages))

            type_ids = set()
            for i, html in enumerate(html_pages, 1):
                recs = parse_results_html(html, source_doc_type=dt)
                soup = BeautifulSoup(html, "html.parser")
                link_count = len(soup.select("a[href*='Document.aspx?DK=']"))
                no_data = bool(re.search(r"No\s+Records|No\s+Results|No data", soup.get_text(" ", strip=True), re.I))
                print(f"   page{i}: parsed={len(recs)} links={link_count} no_data={no_data}")
                for r in recs:
                    if r.get("documentId"):
                        type_ids.add(r["documentId"])
                        all_ids.add(r["documentId"])

            print("  unique IDs in type:", len(type_ids), sorted(type_ids)[:5])
            _goto_document_search(page, verbose=False)

        print("\nTOTAL unique IDs across all selected types:", len(all_ids), sorted(all_ids)[:20])
        browser.close()


if __name__ == "__main__":
    main()
