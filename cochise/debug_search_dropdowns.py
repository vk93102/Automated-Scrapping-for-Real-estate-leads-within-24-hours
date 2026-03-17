#!/usr/bin/env python3
"""Debug: Inspect search page dropdowns after navigation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lapaz.extractor import _goto_document_search, _normalise_date
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        
        # Navigate to search page
        print("[1] Navigating to search page...")
        _goto_document_search(page, verbose=True)
        
        # Save page HTML
        html = page.content()
        Path("debug_search_page.html").write_text(html)
        print("[2] Saved search page HTML to debug_search_page.html")
        
        # Inspect all selects
        print("\n[3] Inspecting all <select> elements on page:")
        selects = page.locator("select").all()
        print(f"    Found {len(selects)} select elements")
        
        for i, sel in enumerate(selects, 1):
            sel_id = page.evaluate("el => el.id", sel.element_handle())
            sel_name = page.evaluate("el => el.name", sel.element_handle())
            opts = page.evaluate(
                "el => Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}))",
                sel.element_handle()
            )
            print(f"\n    [{i}] id={sel_id}, name={sel_name}")
            print(f"        Options: {len(opts)}")
            for opt in opts[:5]:
                print(f"          - {opt['text']} (value={opt['value']})")
            if len(opts) > 5:
                print(f"          ... and {len(opts) - 5} more")
        
        # Try to find document group/type specific selects
        print("\n[4] Checking for cboDocumentGroup and cboDocumentType:")
        for sel_name in ["cboDocumentGroup", "cboDocumentType"]:
            loc = page.locator(f"select[id*='{sel_name}'], select[name*='{sel_name}']")
            count = loc.count()
            print(f"    {sel_name}: found {count}")
            if count > 0:
                opts = loc.first.evaluate(
                    "el => Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}))"
                )
                print(f"      Options: {len(opts)}")
                for opt in opts[:8]:
                    print(f"        - {opt['text']}")
        
        print("\n[5] Current URL:", page.url)
        print("[6] Take screenshot...")
        page.screenshot(path="debug_search_page.png")
        print("    Saved to debug_search_page.png")
        
        browser.close()

if __name__ == "__main__":
    main()
