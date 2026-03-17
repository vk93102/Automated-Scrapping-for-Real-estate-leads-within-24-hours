from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
import importlib.util

sc_path = Path(__file__).resolve().parent / 'extractor.py'
spec = importlib.util.spec_from_file_location('santacruz_extractor', sc_path)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

DK = '736875'

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1366, 'height': 900})
    page = context.new_page()
    sc._base.COUNTY_LABEL = 'SANTA CRUZ'
    sc._base.COUNTY_DISPLAY = 'Santa Cruz'
    sc._base._goto_document_search(page, verbose=False)
    cookie_header = sc._base._cookie_header_from_cookies(context.cookies())
    browser.close()

session = sc._base._make_session(cookie_header)
r = session.get(f'https://www.thecountyrecorder.com/Document.aspx?DK={DK}', timeout=30)
print('status', r.status_code)
html = r.text
print('has ImageHandler', 'ImageHandler.ashx' in html)
print('has btnViewImage', 'btnViewImage' in html)
print('has View Image', 'View Image' in html)
for m in re.findall(r'(ImageHandler\.ashx[^"\']+|btnViewImage|View Image|DocumentImage[^"\']+)', html, re.I)[:20]:
    print('match', m)
Path('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/SANTA CRUZ/output/detail_736875.html').write_text(html, encoding='utf-8')
print('saved html')
