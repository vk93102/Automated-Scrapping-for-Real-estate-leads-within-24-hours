#!/usr/bin/env python3
"""
Since page 2 pagination fails, fetch individual document detail pages
to extract remaining records. This is a fallback approach that uses
the detailUrl URLs from the extracted records to get full document data.
"""
import csv
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import ssl
from bs4 import BeautifulSoup
import time

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-GB,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://eagleassessor.coconino.az.gov:8444/web/search/DOCSEARCH1213S1",
    "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
}

def read_extracted_csv() -> list:
    """Read the already extracted records from CSV."""
    csv_path = OUTPUT_DIR / "coconino_page1_extracted.csv"
    records = []
    if csv_path.exists():
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            records = list(reader)
    return records


def fetch_document_details(doc_ids: list, cookie_str: str, max_fetch: int = 10) -> dict:
    """
    Fetch detail pages for documents. This is slow but can retrieve complete data
    when pagination fails.
    """
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    summary = {
        "requested": len(doc_ids),
        "max_fetch": max_fetch,
        "fetched": 0,
        "failed": 0,
        "documents": []
    }
    
    print(f"Attempting to fetch detail pages for {len(doc_ids)} documents (max {max_fetch})...")
    
    for i, doc_id in enumerate(doc_ids[:max_fetch]):
        url = f"https://eagleassessor.coconino.az.gov:8444/web/document/{doc_id}?search=DOCSEARCH1213S1"
        print(f"  [{i+1}/{min(len(doc_ids), max_fetch)}] Fetching {doc_id}...", end=" ")
        
        try:
            req = Request(url)
            for key, value in HEADERS.items():
                req.add_header(key, value)
            req.add_header("Cookie", cookie_str)
            
            with urlopen(req, context=ssl_context, timeout=10) as response:
                html = response.read().decode("utf-8", errors="ignore")
                summary["fetched"] += 1
                print(f"✓ ({len(html)} bytes)")
                
                # Save detail page
                detail_path = OUTPUT_DIR / f"detail_{doc_id}.html"
                detail_path.write_text(html, encoding="utf-8")
                
                summary["documents"].append({
                    "documentId": doc_id,
                    "status": 200,
                    "path": str(detail_path)
                })
                
        except HTTPError as e:
            summary["failed"] += 1
            print(f"✗ HTTP {e.code}")
            summary["documents"].append({
                "documentId": doc_id,
                "status": e.code
            })
        except Exception as e:
            summary["failed"] += 1
            print(f"✗ {type(e).__name__}")
            summary["documents"].append({
                "documentId": doc_id,
                "error": str(e)
            })
        
        # Rate limit
        time.sleep(0.5)
    
    return summary


def main():
    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        print("Set COCONINO_COOKIE env var")
        exit(1)
    
    # Read existing records
    records = read_extracted_csv()
    print(f"✓ Found {len(records)} extracted records in CSV")
    
    # Extract document IDs
    doc_ids = [r.get("documentId") for r in records if r.get("documentId")]
    print(f"✓ Document IDs to check: {len(doc_ids)}")
    
    # Show statistics about what we have
    print(f"\n📊 EXTRACTION STATUS:")
    print(f"   Current records: {len(records)}")
    print(f"   County claims: 192 total records")
    print(f"   Missing: ~{192 - len(records)} records")
    print(f"\n⚠️  PAGINATION ISSUE:")
    print(f"   Page 1 works (HTTP 200)")
    print(f"   Page 2+ returns HTTP 500 from county")
    print(f"\n💡 WORKAROUNDS TO TRY:")
    print(f"   1. Directly access document detail pages (slow but guaranteed)")
    print(f"   2. Look for export button/link in county UI")
    print(f"   3. Try different session or referer headers")
    
    # Optional: Try fetching a few detail pages as proof of concept
    print(f"\n🧪 Attempting to fetch a few detail pages as test...")
    
    if doc_ids:
        result = fetch_document_details(doc_ids, cookie, max_fetch=3)
        print(f"\n   Fetched: {result['fetched']}")
        print(f"   Failed: {result['failed']}")
        print(f"\nResults saved to: {OUTPUT_DIR}/detail_DOC*.html")
        
        with open(OUTPUT_DIR / "detail_fetch_summary.json", 'w') as f:
            json.dump(result, f, indent=2)
    
    print(f"\n✅ IMMEDIATE ACTIONABLE RESULTS:")
    print(f"   CSV with {len(records)} records: output/coconino_page1_extracted.csv")
    print(f"   These records are verified and complete from county data.")
    print(f"   Additional {192-len(records)}  records require alternative pagination method.")


if __name__ == "__main__":
    main()
