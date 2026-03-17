from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from greenlee.extractor import _goto_document_search, _select_option_containing


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()

        _goto_document_search(page, verbose=True)
        gs = "select[id*='cboDocumentGroup']"
        ts = "select[id*='cboDocumentType']"

        print("group notice select:", _select_option_containing(page, gs, "Notice"))
        if page.locator("input[id*='btnLoadDocumentTypes']").count() > 0:
            page.locator("input[id*='btnLoadDocumentTypes']").first.click()
            page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        opts = page.locator(ts).first.evaluate(
            "el => Array.from(el.options).map(o => (o.text || '').trim()).filter(Boolean)"
        )
        print("options:", len(opts))
        for o in opts:
            u = o.upper()
            if any(k in u for k in ["NOTICE", "DEFAULT", "TRUSTEE", "REINSTATEMENT", "PENDENS"]):
                print(o)

        browser.close()


if __name__ == "__main__":
    main()
