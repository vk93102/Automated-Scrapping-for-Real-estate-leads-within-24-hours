#!/usr/bin/env python3
"""
Maricopa County Pipeline Diagnostic Tool

Helps diagnose:
1. API connectivity and data availability
2. PDF download capability
3. OCR/LLM extraction quality
4. Database integration
5. Output file generation
"""

import os
import sys
import json
import requests
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from maricopa.maricopa_api import search_recording_numbers, fetch_metadata
from maricopa.http_client import new_session, RetryConfig
from maricopa.pdf_downloader import fetch_pdf_bytes


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_api_connectivity():
    """Test if we can reach the Maricopa Recorder API."""
    print_section("API CONNECTIVITY TEST")
    
    try:
        session = requests.Session()
        url = "https://publicapi.recorder.maricopa.gov/documents/search"
        
        # Test with a date range that definitely has data
        params = {
            "beginDate": "2025-03-18",
            "endDate": "2025-03-19",
            "businessNames": "",
            "firstNames": "",
            "lastNames": "",
            "middleNameIs": "",
            "pageNumber": 1,
            "pageSize": 10,
        }
        
        resp = session.get(url, params=params, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("totalResults", 0)
            print(f"✅ API reachable")
            print(f"   Total records found on 2025-03-18: {total}")
            return True
        else:
            print(f"❌ API returned status {resp.status_code}")
            print(f"   Response: {resp.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ API connection failed: {e}")
        return False


def test_document_codes():
    """Test which document codes return results."""
    print_section("DOCUMENT CODE AVAILABILITY TEST")
    
    common_codes = ["DT", "NS", "TR", "UT", "CC", "AS", "ST", "DD"]
    session = new_session()
    retry = RetryConfig(attempts=2, base_sleep_s=0.5, max_sleep_s=5.0)
    
    begin = date(2025, 3, 18)
    end = date(2025, 3, 19)
    
    results = {}
    for code in common_codes:
        try:
            recs = search_recording_numbers(
                session,
                document_codes=[code],
                begin_date=begin,
                end_date=end,
                retry=retry,
            )
            results[code] = len(recs)
            symbol = "✅" if recs else "⚠️"
            print(f"  {symbol} {code}: {len(recs)} records")
        except Exception as e:
            results[code] = None
            print(f"  ❌ {code}: {str(e)[:50]}")
    
    working_codes = [k for k, v in results.items() if v and v > 0]
    print(f"\n✅ Working document codes: {', '.join(working_codes) if working_codes else 'NONE'}")
    
    return working_codes if working_codes else None


def test_metadata_fetch(recording_number: str):
    """Test fetching metadata for a specific recording."""
    print_section(f"METADATA FETCH TEST: {recording_number}")
    
    try:
        session = new_session()
        meta = fetch_metadata(session, recording_number)
        
        print(f"  Recording Number: {meta.recording_number}")
        print(f"  Recording Date: {meta.recording_date}")
        print(f"  Document Codes: {meta.document_codes}")
        print(f"  Names: {meta.names}")
        print(f"  Pages: {meta.page_amount}")
        print(f"  Restricted: {meta.restricted}")
        print("✅ Metadata fetch successful")
        
        return meta
        
    except Exception as e:
        print(f"❌ Failed to fetch metadata: {e}")
        return None


def test_pdf_download(recording_number: str) -> Optional[bytes]:
    """Test downloading a PDF for a recording."""
    print_section(f"PDF DOWNLOAD TEST: {recording_number}")
    
    try:
        session = new_session()
        retry = RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=10.0)
        
        pdf_bytes = fetch_pdf_bytes(session, recording_number, retry=retry)
        
        if pdf_bytes:
            size_kb = len(pdf_bytes) / 1024
            print(f"  Downloaded: {size_kb:.1f} KB")
            print(f"  PDF Header: {pdf_bytes[:10].hex()}")
            print("✅ PDF download successful")
            return pdf_bytes
        else:
            print("⚠️ No PDF bytes returned")
            return None
            
    except Exception as e:
        print(f"❌ PDF download failed: {e}")
        return None


def test_ocr(pdf_bytes: bytes):
    """Test OCR on PDF bytes."""
    print_section("TESSERACT OCR TEST")
    
    try:
        from maricopa.ocr_pipeline import ocr_pdf_pages_tesseract
        
        result = ocr_pdf_pages_tesseract(pdf_bytes, max_pages=2)
        
        if result.get('success'):
            text = result.get('text', '')
            lines = text.split('\n')
            print(f"  Pages processed: {result.get('pages_processed', 0)}")
            print(f"  Text length: {len(text)} chars")
            print(f"  First 300 chars:")
            print(f"    {text[:300]}")
            print("✅ OCR successful")
            return text
        else:
            print(f"❌ OCR failed: {result.get('error', 'Unknown error')}")
            return None
            
    except ImportError:
        print("❌ OCR module not available or Tesseract not installed")
        return None
    except Exception as e:
        print(f"❌ OCR error: {e}")
        return None


def test_llm_extraction(ocr_text: str):
    """Test LLM extraction on OCR text."""
    print_section("LLM EXTRACTION TEST")
    
    try:
        from maricopa.llm_extract import extract_fields_llm
        
        fields = extract_fields_llm(ocr_text)
        
        if fields:
            print(f"  Trustor 1: {fields.trustor_1_full_name}")
            print(f"  Trustor 2: {fields.trustor_2_full_name}")
            print(f"  Address: {fields.property_address}")
            print(f"  City: {fields.address_city}, {fields.address_state} {fields.address_zip}")
            print(f"  Principal: ${fields.original_principal_balance}")
            print(f"  Sale Date: {fields.sale_date}")
            
            extracted_count = sum(1 for v in [
                fields.trustor_1_full_name,
                fields.property_address,
                fields.original_principal_balance,
            ] if v)
            
            print(f"✅ Extracted {extracted_count}/3 key fields")
            return fields
        else:
            print("❌ LLM returned no fields")
            return None
            
    except Exception as e:
        print(f"❌ LLM extraction error: {e}")
        return None


def test_output_files():
    """Check if output files exist and are valid."""
    print_section("OUTPUT FILES TEST")
    
    files_to_check = [
        "output/maricopa_output.json",
        "output/maricopa_properties.csv",
        "output/recording_numbers_found.txt",
    ]
    
    for filepath in files_to_check:
        p = Path(filepath)
        if p.exists():
            size_kb = p.stat().st_size / 1024
            print(f"  ✅ {filepath}: {size_kb:.1f} KB")
            
            # For JSON, check it's valid
            if filepath.endswith('.json'):
                try:
                    with open(p) as f:
                        data = json.load(f)
                    print(f"     ({len(data)} records)")
                except:
                    print(f"     ⚠️ Invalid JSON")
        else:
            print(f"  ⚠️ {filepath}: NOT FOUND")


def run_full_diagnostic():
    """Run complete diagnostic suite."""
    print("\n" + "═"*60)
    print("   MARICOPA COUNTY PIPELINE DIAGNOSTIC")
    print("═"*60)
    
    # Test API
    if not test_api_connectivity():
        print("\n❌ FATAL: Cannot reach API. Check internet connection.")
        return False
    
    # Test document codes
    codes = test_document_codes()
    if not codes:
        print("\n⚠️ WARNING: No document codes returned results on test date")
        print("   This may indicate API issues or data gaps")
        return False
    
    # Get a sample recording
    print_section("GETTING SAMPLE RECORDING")
    session = new_session()
    retry = RetryConfig(attempts=2)
    try:
        sample_recs = search_recording_numbers(
            session,
            document_codes=[codes[0]],
            begin_date=date(2025, 3, 18),
            end_date=date(2025, 3, 19),
            page_size=1,
            max_results=1,
            retry=retry,
        )
        
        if not sample_recs:
            print("❌ Could not get sample recording")
            test_output_files()
            return False
            
        sample_rec = sample_recs[0]
        print(f"  Sample recording: {sample_rec}")
        
    except Exception as e:
        print(f"❌ Failed to get sample: {e}")
        return False
    
    # Test metadata fetch
    meta = test_metadata_fetch(sample_rec)
    if not meta:
        return False
    
    # Test PDF download
    pdf_bytes = test_pdf_download(sample_rec)
    
    if pdf_bytes:
        # Test OCR
        ocr_text = test_ocr(pdf_bytes)
        
        if ocr_text:
            # Test LLM
            test_llm_extraction(ocr_text)
    
    # Test output files
    test_output_files()
    
    print("\n" + "="*60)
    print("  DIAGNOSTIC COMPLETE")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    success = run_full_diagnostic()
    sys.exit(0 if success else 1)
