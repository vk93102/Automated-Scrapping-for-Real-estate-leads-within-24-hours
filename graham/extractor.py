#!/usr/bin/env python3
"""Graham County, AZ pipeline built on shared county-recorder flow."""

from __future__ import annotations

import json
from pathlib import Path

from greenlee import extractor as _base

_base.COUNTY_LABEL = "GRAHAM"
_base.COUNTY_DISPLAY = "Graham"

# Graham-specific detailed LLM extraction prompt (applies via shared Greenlee base extractor).
_base.COUNTY_LLM_SYSTEM_PROMPT = """
Extract Graham recorder document fields from OCR/detail text.

Return STRICT JSON object with keys exactly:
- trustor (string)
- trustee (string)
- beneficiary (string)
- principalAmount (string)
- propertyAddress (string)
- grantors (array of strings)
- grantees (array of strings)
- confidenceNote (string)

NAME RULES:
1) Return only ONE primary real name/entity for trustor/trustee/beneficiary.
2) If multiple names/entities appear, keep first primary one only.
3) Strip descriptors: "as trustee", "dba", "fka", "et al", role boilerplate, mailing text.
4) Keep valid business suffixes only when part of legal name: LLC, INC, CORP, COMPANY, LTD, TRUST, BANK, ASSOCIATION.

ADDRESS RULES:
1) propertyAddress must be the actual US property street address (number + street + optional city/state/zip).
2) Exclude APN/parcel-only, lot/block-only, subdivision-only, legal description, and recording boilerplate.
3) If multiple addresses are present, choose property/situs address only.

AMOUNT RULES:
1) principalAmount format must be "$123,456.78".
2) Only return principalAmount when >= $1,000.

NOT_FOUND RULE:
If not confidently found, use "NOT_FOUND" for string fields and [] for arrays.
Set confidenceNote to "NOT_FOUND:<comma-separated-field-names>".

Do not guess. Do not invent. Return JSON only.
""".strip()

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_base.OUTPUT_DIR = OUTPUT_DIR
_base.STORAGE_STATE_PATH = OUTPUT_DIR / "session_state.json"

DEFAULT_DOCUMENT_TYPES = _base.DEFAULT_DOCUMENT_TYPES
CSV_FIELDS = _base.CSV_FIELDS
sanitize_property_address = _base.sanitize_property_address
sanitize_borrower_name = _base.sanitize_borrower_name


def run_graham_pipeline(*args, **kwargs):
    res = _base.run_greenlee_pipeline(*args, **kwargs)

    csv_path = Path(res.get("csv_path", ""))
    json_path = Path(res.get("json_path", ""))

    ts = Path(csv_path).stem.replace("greenlee_leads_", "") if csv_path else ""
    if ts:
        new_csv = OUTPUT_DIR / f"graham_leads_{ts}.csv"
        new_json = OUTPUT_DIR / f"graham_leads_{ts}.json"
        if csv_path.exists():
            csv_path.rename(new_csv)
            res["csv_path"] = str(new_csv)
        if json_path.exists():
            json_path.rename(new_json)
            res["json_path"] = str(new_json)

    if isinstance(res.get("summary"), dict):
        res["summary"]["county"] = "Graham County, AZ"

    json_out = Path(res.get("json_path", ""))
    if json_out.exists():
        try:
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            if isinstance(payload.get("meta"), dict):
                payload["meta"]["county"] = "Graham County, AZ"
            json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    return res
