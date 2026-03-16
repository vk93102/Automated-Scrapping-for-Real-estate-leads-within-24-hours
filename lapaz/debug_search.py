#!/usr/bin/env python3
"""Debug the search page: find the actual doc type dropdown and its options."""
import sys, json
sys.path.insert(0, "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")

from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path("/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")
OUT  = ROOT / "lapaz/output"
STATE = OUT / "session_state.json"

BASE_URL = "https://www.thecountyrecorder.com"
SEARCH_URL = f"{BASE_URL}/Search.aspx"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(
        storage_state=str(STATE) if STATE.exists() else None,
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.new_page()

    # Navigate to the search page (using existing session)
    print("Navigating to search page...")
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(2000)

    # Flow: Continue -> Arizona -> La Paz -> Accept
    for sel in ["#MainContent_Button1", "input[id*='Button1'][value*='Continue']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=6000)
                page.wait_for_timeout(1500)
                print(f"  Clicked Continue via {sel}")
                break
            except Exception:
                pass

    for select_sel, target in [
        ("select[id*='cboStates'], select[name*='cboStates']", "ARIZONA"),
        ("select[id*='cboCounties'], select[name*='cboCounties']", "LA PAZ"),
    ]:
        if page.locator(select_sel).count() > 0:
            opts = page.locator(f"{select_sel} option").all_text_contents()
            print(f"  Dropdown {select_sel}: {opts[:5]}...")
            for op in opts:
                if target in (op or "").upper():
                    page.locator(select_sel).first.select_option(label=op.strip(), timeout=4000)
                    page.wait_for_timeout(600)
                    print(f"    Selected: {op.strip()}")
                    break

    for sel in ["input[type='submit'][value*='Continue']", "#MainContent_searchMainContent_ctl01_ctl00_btnContinue"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=6000)
                page.wait_for_timeout(1500)
                print(f"  Clicked Continue (after county) via {sel}")
                break
            except Exception:
                pass

    for sel in ["#MainContent_searchMainContent_ctl01_btnAccept", "input[id*='btnAccept']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=6000)
                page.wait_for_timeout(1500)
                print(f"  Accepted disclaimer via {sel}")
                break
            except Exception:
                pass

    print(f"\nCurrent URL: {page.url}")

    # Now inspect the document type dropdown
    print("\n=== Document Type Dropdown ===")
    for sel in [
        "select[id*='cboDocumentType']",
        "select[name*='cboDocumentType']",
        "select[id*='DocumentType']",
        "select[name*='DocumentType']",
        "select",
    ]:
        if page.locator(sel).count() > 0:
            ids = page.locator(sel).evaluate_all("els => els.map(e => ({id:e.id, name:e.name, count:e.options.length}))")
            print(f"  Found selects matching '{sel}': {ids}")
            for info in ids:
                sel_id = info.get("id") or info.get("name")
                if sel_id:
                    opts = page.locator(f"#{sel_id} option" if info.get("id") else f"select[name='{info['name']}'] option").all_text_contents()
                    print(f"  Options for #{sel_id or info.get('name')} ({len(opts)} total):")
                    for o in opts[:30]:
                        print(f"    - {repr(o)}")
            break

    # Save page HTML for inspection
    html = page.content()
    (OUT / "debug_search_page.html").write_text(html, encoding="utf-8")
    print(f"\nSaved search page HTML: {len(html)} bytes")

    # Also check all form inputs
    print("\n=== Form inputs on search page ===")
    inputs = page.evaluate("""
        () => Array.from(document.querySelectorAll('input[type=text], select')).map(e => ({
            tag: e.tagName,
            id: e.id,
            name: e.name,
            type: e.type || '',
            value: e.value
        }))
    """)
    for inp in inputs:
        print(f"  <{inp['tag']} id={inp['id']!r} name={inp['name']!r} type={inp['type']!r} value={inp['value'][:40]!r}>")

    ctx.storage_state(path=str(STATE))
    browser.close()

print("\nDone.")
