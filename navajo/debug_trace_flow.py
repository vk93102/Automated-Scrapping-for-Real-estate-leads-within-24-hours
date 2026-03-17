#!/usr/bin/env python3
"""Debug: Trace exact flow step by step, save at each stage."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.thecountyrecorder.com"

def save_state(page, step_name):
    """Save page state for inspection."""
    print(f"\n>>> {step_name}")
    try:
        print(f"    URL: {page.url}")
        html = page.content()
        if html:
            Path(f"debug_step_{step_name.replace(' ', '_')}.html").write_text(html)
            print(f"    ✓ Saved HTML ({len(html)} bytes)")
    except Exception as e:
        print(f"    ⚠️  Could not save: {e}")

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page()
        
        # STEP 1: Go to Search
        page.goto(f"{BASE_URL}/Search.aspx", wait_until="domcontentloaded", timeout=120_000)
        save_state(page, "01_Search.aspx")
        
        # STEP 2: Click Continue
        btn = page.locator("#MainContent_Button1").first
        if btn.count() > 0:
            print("  Clicking Continue...")
            btn.click(timeout=8000)
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)
        save_state(page, "02_After_Continue")
        
        # STEP 3: Select ARIZONA
        state_sel = page.locator("select[id*='cboStates']").first
        if state_sel.count() > 0:
            print("  Selecting ARIZONA...")
            state_sel.select_option(label="ARIZONA")
            page.wait_for_timeout(1000)
        save_state(page, "03_After_Select_State")
        
        # STEP 4: Select LA PAZ
        county_sel = page.locator("select[id*='cboCounties']").first
        if county_sel.count() > 0:
            opts = county_sel.evaluate(
                "el => Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}))"
            )
            print(f"  County options: {[o['text'] for o in opts]}")
            # County selection may auto-navigate, so wait for load state
            print(f"  >>> Selecting LA PAZ (may auto-navigate)...")
            county_sel.select_option(label="LA PAZ")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception as e:
                print(f"    Navigation wait error (expected): {e}")
            page.wait_for_timeout(3000)
            print(f"    County selection complete")
        save_state(page, "04_After_Select_County")
        
        # STEP 5: Check for Go button
        print("\n  Looking for Go button...")
        go_btns = page.locator("input[value='Go']").all()
        print(f"    Found {len(go_btns)} 'Go' buttons")
        for i, btn in enumerate(go_btns, 1):
            val = page.evaluate("el => el.value", btn.element_handle())
            vis = page.evaluate("el => el.offsetParent !== null", btn.element_handle())
            print(f"      [{i}] value={val}, visible={vis}")
            if vis:
                print(f"  >>> Clicking Go button...")
                btn.click(timeout=8000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2000)
                break
        save_state(page, "05_After_Click_Go")
        
        # STEP 6: Look for disclaimer Accept button
        print("\n  Looking for Accept button...")
        accept_btns = page.locator("input[id*='btnAccept']").all()
        print(f"    Found {len(accept_btns)} Accept buttons")
        for btn in accept_btns:
            vis = page.evaluate("el => el.offsetParent !== null", btn.element_handle())
            if vis:
                print(f"  >>> Clicking Accept button...")
                btn.click(timeout=8000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2000)
                break
        save_state(page, "06_After_Accept_Disclaimer")
        
        # STEP 7: Navigate to search document if needed
        if "Search.aspx" not in page.url:
            print(f"\n  Not on Search.aspx, looking for link...")
            for sel in ["a:has-text('Search Document')", "a[href*='Search.aspx']"]:
                if page.locator(sel).count() > 0:
                    print(f"    Found with: {sel}")
                    page.locator(sel).first.click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    break
        save_state(page, "07_Final_Search_Form")
        
        # STEP 8: Check for search form
        print("\n  Looking for search form elements:")
        for pattern in ["cboDocumentGroup", "cboDocumentType", "tbDateStart", "tbDateEnd"]:
            locs = page.locator(f"select[id*='{pattern}'], input[id*='{pattern}'], select[name*='{pattern}'], input[name*='{pattern}']").all()
            print(f"    {pattern}: {len(locs)} found")
        
        print("\n  [Done] Browser will stay open. Check debug_step_*.html files.")
        page.wait_for_timeout(5000)
        browser.close()

if __name__ == "__main__":
    main()
