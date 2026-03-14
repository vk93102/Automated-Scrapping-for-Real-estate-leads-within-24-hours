#!/usr/bin/env python3
"""Quick test of the new URL-discovery helpers in extractor.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from extractor import (
    _extract_pdf_guid_from_html,
    _extract_iframe_pdf_path,
    BASE_URL,
)

# ── Test 1: pdfjs link in detail page ─────────────────────────────────────
fake_detail = (
    '<a href="/web/document-image-pdfjs/DOC1870S924/'
    '536801f5-cb31-4c0e-b671-550225b67795/4035050.pdf?allowDownload=true&index=1">'
    'View PDF</a>'
)
result = _extract_pdf_guid_from_html(fake_detail)
assert result is not None, "Expected GUID extraction to succeed"
guid, fname = result
expected_url = (
    f"{BASE_URL}/web/document-image-pdf/DOC1870S924/"
    f"536801f5-cb31-4c0e-b671-550225b67795/4035050-1.pdf?index=1"
)
constructed = f"{BASE_URL}/web/document-image-pdf/DOC1870S924/{guid}/{fname}-1.pdf?index=1"
assert constructed == expected_url, f"URL mismatch:\n  got: {constructed}\n  exp: {expected_url}"
print(f"[OK] Test 1 — GUID extraction from detail page")
print(f"     GUID     : {guid}")
print(f"     Filename : {fname}")
print(f"     URL      : {constructed}")

# ── Test 2: iframe src in pdfjs viewer page ────────────────────────────────
fake_pdfjs = (
    '<iframe src="/web/document-image-pdf/DOC2352S262/'
    '1b3bbb90-f82c-4b57-9f36-1eaa6e8cfd39/2026-002408-1.pdf?index=1"></iframe>'
)
iframe_path = _extract_iframe_pdf_path(fake_pdfjs)
assert iframe_path is not None, "Expected iframe path extraction to succeed"
assert "document-image-pdf" in iframe_path
print(f"[OK] Test 2 — iframe PDF path extraction")
print(f"     Path: {iframe_path}")
print(f"     Full: {BASE_URL}{iframe_path}")

# ── Test 3: alternative URL variant (the recording-number filename style) ──
fake_detail2 = (
    '<a href="/web/document-image-pdfjs/DOC1884S23/'
    'abcd1234-ab12-ab12-ab12-abcdef012345/2026-001234.pdf?allowDownload=true&index=1">'
    'Download</a>'
)
result2 = _extract_pdf_guid_from_html(fake_detail2)
assert result2 is not None
guid2, fname2 = result2
url2 = f"{BASE_URL}/web/document-image-pdf/DOC1884S23/{guid2}/{fname2}-1.pdf?index=1"
print(f"[OK] Test 3 — recording-number filename style")
print(f"     URL: {url2}")

print("\nAll tests passed ✓")
