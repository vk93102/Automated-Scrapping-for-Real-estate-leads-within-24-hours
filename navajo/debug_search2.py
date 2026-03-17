#!/usr/bin/env python3
"""Navigate to La Paz search page and save the HTML with doc type dropdown visible."""
import sys
sys.path.insert(0, "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")

from pathlib import Path
from playwright.sync_api import sync_playwright
from lapaz.extractor import _select_option_containing, SEARCH_URL, STORAGE_STATE_PATH, OUTPUT_DIR

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        storage_state=str(STORAGE_STATE_PATH) if STORAGE_STATE_PATH.exists() else None,
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.new_page()
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(2000)

    # Continue
    for sel in ["#MainContent_Button1", "input[id*='Button1'][value*='Continue']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=6000)
                page.wait_for_timeout(1500)
                print(f"Continue clicked")
                break
            except Exception:
                pass

    # State + County
    for select_sel, target in [
        ("select[id*='cboStates'], select[name*='cboStates']", "ARIZONA"),
        ("select[id*='cboCounties'], select[name*='cboCounties']", "LA PAZ"),
    ]:
        if page.locator(select_sel).count() > 0:
            ok = _select_option_containing(page, select_sel, target)
            print(f"  Select '{target}': {'ok' if ok else 'FAIL'}")
            page.wait_for_timeout(600)

    # Continue after county
    for sel in ["input[type='submit'][value*='Continue']", "#MainContent_searchMainContent_ctl01_ctl00_btnContinue"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=6000)
                page.wait_for_timeout(1500)
                print("Continue (post-county) clicked")
                break
            except Exception:
                pass

    # Accept disclaimer
    for sel in ["#MainContent_searchMainContent_ctl01_btnAccept", "input[id*='btnAccept']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=6000)
                page.wait_for_timeout(1500)
                print("Disclaimer accepted")
                break
            except Exception:
                pass

    print(f"URL after nav: {page.url}")

    # Dump ALL select options via JS
    selects_info = page.evaluate("""
        () => Array.from(document.querySelectorAll('select')).map(sel => ({
            id: sel.id,
            name: sel.name,
            options: Array.from(sel.options).map(o => ({text: o.text.trim(), value: o.value}))
        }))
    """)

    for si in selects_info:
        print(f"\n<select id={si['id']!r} name={si['name']!r}> ({len(si['options'])} options)")
        for opt in si["options"][:80]:
            print(f"  value={opt['value']!r:30s}  text={opt['text']!r}")

    # Save page HTML
    html = page.content()
    out = OUTPUT_DIR / "debug_search_page.html"
    out.write_text(html, encoding="utf-8")
    print(f"\nSaved: {out}")

    ctx.storage_state(path=str(STORAGE_STATE_PATH))
    browser.close()
