import os, requests, json
from greenlee.extractor import _groq_extract_fields
from pathlib import Path

[os.environ.__setitem__(k.strip(), v.strip().strip('"').strip("'")) for l in Path('.env').read_text().splitlines() if l.strip() and not l.startswith('#') and '=' in l for k,v in [l.split('=',1)]]

api_key = os.environ.get("GROQ_API_KEY")
print(f"Key loaded: {len(api_key)}")

try:
    data, model = _groq_extract_fields(
        document_id="doc1",
        recording_number="rec1",
        document_type="TEST",
        ocr_text="Lorem ipsum " * 1000,
        detail_text="Detail " * 100,
        api_key=api_key
    )
    print("SUCCESS")
except Exception as e:
    print("FAILED:", e)
