#!/usr/bin/env python3
"""Parse and display the cached search results HTML in a readable table."""

from pathlib import Path
import re

TARGET_TYPES = {
    "LIS PENDENS",
    "TRUSTEES DEED",
    "SHERIFFS DEED",
    "TREASURERS DEED",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
}

html_path = Path(__file__).parent / "output" / "requests_search_results_page1.html"
if not html_path.exists():
    print(f"ERROR: File not found: {html_path}")
    raise SystemExit(1)

html = html_path.read_text(errors="ignore")
print(f"File : {html_path.name}  ({len(html):,} bytes)\n")

# Check summary bar
summary_match = re.search(r'(\d+)\s*-\s*(\d+)\s*of\s*(\d+)', html)
if summary_match:
    print(f"Server says: records {summary_match.group(1)}–{summary_match.group(2)} of {summary_match.group(3)} total\n")

# Extract each row
rows = re.findall(
    r'data-documentid="([^"]+)".*?<h1>(.*?)</h1>',
    html,
    re.DOTALL,
)

print(f"{'#':<4} {'DOC ID':<18} {'Rec #':<10} {'Document Type':<38} {'Date / Time':<24} {'MATCH?'}")
print("-" * 105)

matched = 0
for i, (doc_id, h1_raw) in enumerate(rows, 1):
    parts = re.split(r"&#149;", h1_raw)
    parts = [re.sub(r"\s+", " ", p.replace("&nbsp;", "").replace("\n", "").strip()) for p in parts]
    parts = [p for p in parts if p]
    rec_num  = parts[0] if len(parts) > 0 else ""
    doc_type = parts[1] if len(parts) > 1 else ""
    date_str = parts[2] if len(parts) > 2 else ""
    is_match = doc_type.upper() in TARGET_TYPES
    if is_match:
        matched += 1
    marker = "✅ YES" if is_match else ""
    print(f"{i:<4} {doc_id:<18} {rec_num:<10} {doc_type:<38} {date_str:<24} {marker}")

print("-" * 105)
print(f"\nTotal rows on page: {len(rows)}")
print(f"Rows matching target doc types: {matched}")
print()
if matched == len(rows):
    print("✅ Filter is WORKING — all results match the target document types.")
elif matched == 0:
    print("❌ Filter NOT applied — zero matches (this HTML was fetched with the OLD broken payload).")
    print("   → Re-run bootstrap_session_and_fetch.py to get properly filtered results.")
else:
    print(f"⚠️  Partial filter — {matched}/{len(rows)} match. Server may be returning extra types.")
