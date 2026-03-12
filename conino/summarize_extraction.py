#!/usr/bin/env python3
"""
Extract all records from page_1 then check if county has bulk export or if we need
to handle pagination differently. Since page 2 AJAX returns 500, we'll try:
1. JavaScript-rendered pagination (scroll/load-more)
2. Native CSV export if available
3. Looping through individual document detail pages as fallback
"""
import json
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# Read the page 1 extraction results
page1_json_path = OUTPUT_DIR / "coconino_latest_page1.json"
if page1_json_path.exists():
    with open(page1_json_path) as f:
        data = json.load(f)
    
    print("="*70)
    print("PAGE 1 EXTRACTION SUMMARY")
    print("="*70)
    print(f"✓ Records extracted: {data.get('recordCount', 0)}")
    print(f"✓ CSV file: {data.get('csvFile', 'N/A')}")
    print(f"  Total results claimed by county: {data['summary'].get('totalResults', 0)}")
    print(f"  Pages in search: {data['summary'].get('pageCount', 0)}")
    print(f"  Filter: {data['summary'].get('filterSummary', 'N/A')}")
    
    # Show record sample
    if data.get('records'):
        print(f"\n📋 Record Sample (first 3):")
        for i, rec in enumerate(data['records'][:3], 1):
            print(f"\n   {i}. {rec.get('documentType', 'N/A')} - {rec.get('recordingDate', 'N/A')}")
            print(f"      Doc: {rec.get('documentId', 'N/A')}")
            print(f"      Grantors: {', '.join(rec.get('grantors', []))}")
            print(f"      Grantees: {', '.join(rec.get('grantees', [])[:2])}...")
    
    missing_records = data['summary'].get('totalResults', 0) - data.get('recordCount', 0)
    print(f"\n⚠️  Missing records: {missing_records} of {data['summary'].get('totalResults', 0)}")
    
    if missing_records > 0:
        print("\n📌 NEXT STEPS:")
        print("  1. The county returned 200 OK for page 1 with complete headers ✓")
        print("  2. Page 2 AJAX requests return HTTP 500 from county server")
        print("  3. POST pagination attempts return empty responses")
        print("  4. Options to retrieve remaining records:")
        print("     • Check county website for native 'Export All' button/link")
        print("     • Query individual document detail URLs (DOC IDs) and scrape")
        print("     • Inspect browser JavaScript to see how pagination works")
        print("     •Try increasing the limit parameter if it exists")
        
        print(f"\n💾 Current CSV saved to: {data.get('csvPath', 'N/A')}")
        print(f"   Contains {data.get('recordCount', 0)} records")
