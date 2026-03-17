#!/usr/bin/env python3
"""Discover all document types under each group via AJAX loading."""
import sys, json
sys.path.insert(0, "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")

from pathlib import Path
from playwright.sync_api import sync_playwright
from lapaz.extractor import _select_option_containing, SEARCH_URL, STORAGE_STATE_PATH, OUTPUT_DIR

GROUPS_OF_INTEREST = [
    ("7|Notice",  "Notice"),
    ("2|Deed",    "Deed"),
    ("3|Lien",    "Lien"),
    ("9|Release", "Release"),
    ("11|Court",  "Court"),
]

all_types: dict = {}

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        storage_state=str(STORAGE_STATE_PATH) if STORAGE_STATE_PATH.exists() else None,
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.new_page()

    def nav_to_search():
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(1500)
        for sel in ["#MainContent_Button1", "input[id*='Button1'][value*='Continue']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    break
                except Exception:
                    pass
        for select_sel, target in [
            ("select[id*='cboStates']", "ARIZONA"),
            ("select[id*='cboCounties']", "LA PAZ"),
        ]:
            if page.locator(select_sel).count() > 0:
                ok = _select_option_containing(page, select_sel, target)
                page.wait_for_timeout(400)
        for sel in ["input[type='submit'][value*='Continue']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    break
                except Exception:
                    pass
        for sel in ["input[id*='btnAccept']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    break
                except Exception:
                    pass

    nav_to_search()
    print(f"URL: {page.url}")

    group_sel = "select[id*='cboDocumentGroup']"
    type_sel  = "select[id*='cboDocumentType']"

    for group_value, group_label in GROUPS_OF_INTEREST:
        print(f"\n=== Group: {group_label} (value={group_value}) ===")
        try:
            page.locator(group_sel).first.select_option(value=group_value, timeout=5000)
            # Wait for AJAX: cboDocumentType should stop showing "Loading..."
            page.wait_for_function(
                f"""() => {{
                    const sel = document.querySelector('select[id*="cboDocumentType"]');
                    if (!sel) return false;
                    return Array.from(sel.options).some(o => o.text.trim() !== '' && o.text.trim() !== 'Loading...');
                }}""",
                timeout=10000,
            )
            page.wait_for_timeout(500)
            opts = page.locator(type_sel).first.evaluate(
                "el => Array.from(el.options).map(o => ({text: o.text.trim(), value: o.value}))"
            )
            all_types[group_label] = opts
            for opt in opts:
                print(f"  value={opt['value']!r:30s}  text={opt['text']!r}")
        except Exception as e:
            print(f"  Error: {e}")

    ctx.storage_state(path=str(STORAGE_STATE_PATH))
    browser.close()

# Save results
out = OUTPUT_DIR / "debug_doc_types.json"
out.write_text(json.dumps(all_types, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
