#!/usr/bin/env python3
"""Quick OCR test using saved session cookies and fixed extractor functions."""
import sys
sys.path.insert(0, "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")

import json
from pathlib import Path
from lapaz.extractor import (
    _make_session,
    _cookie_header_from_cookies,
    fetch_detail,
    discover_image_urls,
    ocr_document_images,
    enrich_record,
    _regex_principal,
    _regex_address,
)

ROOT = Path("/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours")
state_path = ROOT / "lapaz/output/session_state.json"
cookies = json.loads(state_path.read_text())["cookies"]
cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
session = _make_session(cookie_header)

# Test a few DKs including one that should be a NOTICE OF TRUSTEE SALE
# Find one from the latest CSV
import csv
csv_files = sorted((ROOT / "lapaz/output").glob("lapaz_leads_*.csv"))
latest_csv = csv_files[-1] if csv_files else None
nts_dks = []
cert_dks = []
if latest_csv:
    with latest_csv.open() as f:
        for row in csv.DictReader(f):
            dt = row.get("documentType", "").upper()
            dk = row.get("documentId", "")
            if "NOTICE OF TRUSTEE" in dt and len(nts_dks) < 3:
                nts_dks.append(dk)
            elif "CERT" in dt and len(cert_dks) < 2:
                cert_dks.append(dk)

print(f"NOTICE OF TRUSTEE SALE DKs: {nts_dks}")
print(f"CERT OF REDEMPTION DKs: {cert_dks}")
print()

# Test both types
test_dks = (nts_dks[:2] or []) + (cert_dks[:1] or [])
if not test_dks:
    test_dks = ["274473", "274474"]

for dk in test_dks:
    print(f"\n{'='*60}")
    print(f"DK={dk}")
    print('='*60)

    # Build minimal record
    record = {
        "documentId": dk,
        "recordingNumber": "",
        "recordingDate": "",
        "documentType": "",
        "grantors": "",
        "grantees": "",
        "trustor": "",
        "trustee": "",
        "beneficiary": "",
        "principalAmount": "",
        "propertyAddress": "",
        "detailUrl": f"https://www.thecountyrecorder.com/Document.aspx?DK={dk}",
        "imageUrls": "",
        "ocrMethod": "",
        "ocrChars": 0,
        "sourceCounty": "La Paz",
        "analysisError": "",
    }

    # Run full enrichment
    enriched = enrich_record(record, session, use_groq=False, groq_api_key="", max_image_pages=8)
    print(f"  recordingNumber : {enriched.get('recordingNumber')}")
    print(f"  recordingDate   : {enriched.get('recordingDate')}")
    print(f"  documentType    : {enriched.get('documentType')}")
    print(f"  grantors        : {enriched.get('grantors')}")
    print(f"  grantees        : {enriched.get('grantees')}")
    print(f"  trustor         : {enriched.get('trustor')}")
    print(f"  trustee         : {enriched.get('trustee')}")
    print(f"  beneficiary     : {enriched.get('beneficiary')}")
    print(f"  principalAmount : {enriched.get('principalAmount')}")
    print(f"  propertyAddress : {enriched.get('propertyAddress')}")
    print(f"  imageUrls       : {enriched.get('imageUrls')}")
    print(f"  ocrMethod       : {enriched.get('ocrMethod')}")
    print(f"  ocrChars        : {enriched.get('ocrChars')}")
    print(f"  analysisError   : {enriched.get('analysisError')}")
