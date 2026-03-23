#!/usr/bin/env python3
"""Post-process Greenlee extracted fields without dropping any records.

- Keeps ALL input records.
- Filters/sanitizes only field values (address + party names).
- Writes a new JSON payload with same structure: {meta, count, records}.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from greenlee.extractor import sanitize_borrower_name, sanitize_property_address


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).strip()


def _filter_record(rec: dict) -> dict:
    out = dict(rec)

    addr_raw = _norm(out.get("propertyAddress", ""))
    trustor_raw = _norm(out.get("trustor", ""))
    trustee_raw = _norm(out.get("trustee", ""))
    beneficiary_raw = _norm(out.get("beneficiary", ""))

    addr_clean = sanitize_property_address(addr_raw)
    trustor_clean = sanitize_borrower_name(trustor_raw)
    trustee_clean = sanitize_borrower_name(trustee_raw)
    beneficiary_clean = sanitize_borrower_name(beneficiary_raw)

    # Keep original when sanitizer cannot confidently improve.
    out["propertyAddress"] = addr_clean or addr_raw
    out["trustor"] = trustor_clean or trustor_raw
    out["trustee"] = trustee_clean or trustee_raw
    out["beneficiary"] = beneficiary_clean or beneficiary_raw

    # Optional trace fields for diagnostics.
    out["propertyAddressFiltered"] = bool(addr_clean and addr_clean != addr_raw)
    out["trustorFiltered"] = bool(trustor_clean and trustor_clean != trustor_raw)
    out["trusteeFiltered"] = bool(trustee_clean and trustee_clean != trustee_raw)
    out["beneficiaryFiltered"] = bool(beneficiary_clean and beneficiary_clean != beneficiary_raw)

    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Filter Greenlee extracted fields (no record deletion)")
    p.add_argument("--input", required=True, help="Input JSON file (greenlee output format)")
    p.add_argument("--output", required=True, help="Output JSON file")
    args = p.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    payload = json.loads(in_path.read_text(encoding="utf-8"))
    records = payload.get("records", []) if isinstance(payload, dict) else []

    filtered = [_filter_record(r if isinstance(r, dict) else {}) for r in records]

    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    meta = dict(meta)
    meta["fieldFilter"] = "greenlee/filter_extracted_fields.py"
    meta["fieldFilterKeepAllRecords"] = True

    out_payload = {
        "meta": meta,
        "count": len(filtered),
        "records": filtered,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")

    with_addr = sum(1 for r in filtered if _norm(r.get("propertyAddress", "")))
    with_trustor = sum(1 for r in filtered if _norm(r.get("trustor", "")))
    print(f"input_records={len(records)} output_records={len(filtered)}")
    print(f"with_address={with_addr} with_trustor={with_trustor}")
    print(f"wrote={out_path}")


if __name__ == "__main__":
    main()
