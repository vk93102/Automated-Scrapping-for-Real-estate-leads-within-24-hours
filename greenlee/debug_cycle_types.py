from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lapaz.extractor import _goto_document_search, _select_option_containing


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()

        gs = "select[id*='cboDocumentGroup']"
        ts = "select[id*='cboDocumentType']"

        # First run
        _goto_document_search(page, verbose=False)
        page.locator("input[id*='tbDateStart']").first.fill("2/1/2026")
        page.locator("input[id*='tbDateEnd']").first.fill("3/16/2026")
        print("cycle1 group Notice:", _select_option_containing(page, gs, "Notice"))
        if page.locator("input[id*='btnLoadDocumentTypes']").count() > 0:
            page.locator("input[id*='btnLoadDocumentTypes']").first.click()
            page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1200)
        opts1 = page.locator(ts).first.evaluate("el => Array.from(el.options).map(o => (o.text||'').trim())")
        print("cycle1 options count:", len(opts1))
        print("cycle1 has NOD:", any("NOTICE OF DEFAULT" in x.upper() for x in opts1))
        print("cycle1 has NTS:", any("TRUSTEE" in x.upper() and "SALE" in x.upper() for x in opts1))
        print("select NOD:", _select_option_containing(page, ts, "NOTICE OF DEFAULT"))

        page.locator("input[id*='btnSearchDocuments']").first.click()
        page.wait_for_load_state("domcontentloaded")

        # Second run
        _goto_document_search(page, verbose=False)
        page.locator("input[id*='tbDateStart']").first.fill("2/1/2026")
        page.locator("input[id*='tbDateEnd']").first.fill("3/16/2026")
        print("cycle2 group Notice:", _select_option_containing(page, gs, "Notice"))
        if page.locator("input[id*='btnLoadDocumentTypes']").count() > 0:
            page.locator("input[id*='btnLoadDocumentTypes']").first.click()
            page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1200)
        opts2 = page.locator(ts).first.evaluate("el => Array.from(el.options).map(o => (o.text||'').trim())")
        print("cycle2 options count:", len(opts2))
        print("cycle2 has NOD:", any("NOTICE OF DEFAULT" in x.upper() for x in opts2))
        print("cycle2 has NTS:", any("TRUSTEE" in x.upper() and "SALE" in x.upper() for x in opts2))
        print("select NTS:", _select_option_containing(page, ts, "NOTICE OF TRUSTEE SALE"))

        browser.close()


if __name__ == "__main__":
    main()
