#!/usr/bin/env python3
"""Parse saved search page HTML to find all dropdown options."""
from pathlib import Path
from bs4 import BeautifulSoup

html_path = Path("/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/lapaz/output/debug_search_page.html")
if not html_path.exists():
    print("No debug_search_page.html — need to run debug_search.py first with headful mode disabled")
    exit(1)

soup = BeautifulSoup(html_path.read_text(), "html.parser")
for sel in soup.find_all("select"):
    sel_id = sel.get("id", "")
    sel_name = sel.get("name", "")
    opts = [(o.get("value", ""), o.get_text(strip=True)) for o in sel.find_all("option")]
    print(f"\n<select id={sel_id!r} name={sel_name!r}> ({len(opts)} options)")
    for val, text in opts[:60]:
        print(f"  value={val!r:30s} text={text!r}")
