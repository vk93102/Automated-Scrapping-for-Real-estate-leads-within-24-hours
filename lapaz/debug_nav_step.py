#!/usr/bin/env python3
"""Debug: Step-by-step navigation with state inspection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.thecountyrecorder.com"
SEARCH_URL = f"{BASE_URL}/Search.aspx"

def inspect_page(page, label):
    """Print page state."""
    print(f"\n[{label}]")
    print(f"  URL: {page.url}")
    
    # List all buttons
    buttons = page.locator("input[type='button'], input[type='submit']").all()
    print(f"  Buttons: {len(buttons)}")
    for b in buttons[:5]:
        val = page.evaluate("el => el.value", b.element_handle())
        print(f"    - {val}")
    
    # List all selects
    selects = page.locator("select").all()
    print(f"  Selects: {len(selects)}")
    for i, s in enumerate(selects, 1):
        sel_id = page.evaluate("el => el.id", s.element_handle())
        opts = page.evaluate("el => el.options.length", s.element_handle())
        print(f"    [{i}] id={sel_id}, options={opts}")
    
    # First 50 visible text
    visible_text = page.locator("body").first.inner_text()[:500]
    print(f"  Visible text preview:\n    {visible_text.replace(chr(10), chr(10) + '    ')}")

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page()
        
        print("[STEP 1] Go to Search.aspx")
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)
        inspect_page(page, "After Search.aspx load")
        page.wait_for_timeout(2000)
        
        print("\n[STEP 2] Click Continue button")
        for sel in ["#MainContent_Button1", "input[id*='Button1']"]:
            if page.locator(sel).count() > 0:
                print(f"  Found button with selector: {sel}")
                page.locator(sel).first.click(timeout=8000)
                page.wait_for_timeout(2000)
                break
        
        inspect_page(page, "After Continue click")
        
        print("\n[STEP 3] Try to select State dropdown")
        state_sel = "select[id*='cboStates'], select[name*='cboStates']"
        if page.locator(state_sel).count() > 0:
            print(f"  Found state select")
            opts = page.locator(state_sel).first.evaluate(
                "el => Array.from(el.options).map(o => o.text.trim())"
            )
            print(f"  State options: {opts[:5]}")
            page.locator(state_sel).first.select_option(label="ARIZONA", timeout=5000)
            page.wait_for_timeout(1000)
        else:
            print(f"  ⚠️  State select NOT FOUND with: {state_sel}")
        
        inspect_page(page, "After State selection")
        
        print("\n[STEP 4] Try to select County dropdown")
        county_sel = "select[id*='cboCounties'], select[name*='cboCounties']"
        if page.locator(county_sel).count() > 0:
            print(f"  Found county select")
            opts = page.locator(county_sel).first.evaluate(
                "el => Array.from(el.options).map(o => o.text.trim())"
            )
            print(f"  County options: {opts}")
            page.locator(county_sel).first.select_option(label="LA PAZ", timeout=5000)
            page.wait_for_timeout(1000)
        else:
            print(f"  ⚠️  County select NOT FOUND with: {county_sel}")
        
        inspect_page(page, "After County selection")
        page.wait_for_timeout(3000)
        
        print("\n[STEP 5] Look for search form (date/doc type selects)")
        # Check if we see cboDocumentGroup or cboDocumentType
        for name in ["cboDocumentGroup", "cboDocumentType"]:
            sel = f"select[id*='{name}'], select[name*='{name}']"
            count = page.locator(sel).count()
            print(f"  {name}: {count} found")
        
        print("\n[DONE] Press Ctrl+C to close browser or wait 30s...")
        print("(auto-closing in 2s)")
        page.wait_for_timeout(2000)
        browser.close()

if __name__ == "__main__":
    main()
