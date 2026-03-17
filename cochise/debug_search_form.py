#!/usr/bin/env python3
"""Debug: Inspect search form on Search.aspx after full navigation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lapaz.extractor import _goto_document_search
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page()
        
        print("[1] Running _goto_document_search()...")
        _goto_document_search(page, verbose=True)
        
        print(f"\n[2] Current URL: {page.url}")
        
        print("\n[3] Full page text (first 1000 chars):")
        text = page.locator("body").first.inner_text()[:1000]
        print(f"    {text}")
        
        print("\n[4] All select elements:")
        selects = page.locator("select").all()
        print(f"    Found: {len(selects)}")
        for i, sel in enumerate(selects, 1):
            sel_id = page.evaluate("el => el.id", sel.element_handle())
            sel_name = page.evaluate("el => el.name", sel.element_handle())
            print(f"    [{i}] id={sel_id}, name={sel_name}")
        
        print("\n[5] Check for document group/type specifically:")
        for pattern in ["cboDocumentGroup", "cboDocumentType", "DocumentGroup", "DocumentType"]:
            locs = page.locator(f"select[id*='{pattern}'], select[name*='{pattern}'], select[id*='{pattern.lower()}'], select[name*='{pattern.lower()}']").all()
            if locs:
                print(f"    Found {len(locs)} for '{pattern}':")
                for loc in locs:
                    sel_id = page.evaluate("el => el.id", loc.element_handle())
                    sel_name = page.evaluate("el => el.name", loc.element_handle())
                    print(f"      id={sel_id}, name={sel_name}")
        
        print("\n[6] Save page HTML for inspection...")
        html = page.content()
        Path("debug_search_form.html").write_text(html)
        print("    Saved to debug_search_form.html")
        
        print("\n[7] Take screenshot...")
        page.screenshot(path="debug_search_form.png")
        print("    Saved to debug_search_form.png")
        
        print("\n[8] Waiting 3s before close...")
        page.wait_for_timeout(3000)
        browser.close()

if __name__ == "__main__":
    main()
