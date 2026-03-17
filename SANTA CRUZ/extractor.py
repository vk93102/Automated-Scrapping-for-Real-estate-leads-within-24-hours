#!/usr/bin/env python3
"""Santa Cruz County, AZ pipeline built on shared county-recorder flow."""

from __future__ import annotations

import json
from pathlib import Path

from greenlee import extractor as _base

# County-specific overrides
_base.COUNTY_LABEL = "SANTA CRUZ"
_base.COUNTY_DISPLAY = "Santa Cruz"

# Keep outputs isolated in this county folder
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_base.OUTPUT_DIR = OUTPUT_DIR
_base.STORAGE_STATE_PATH = OUTPUT_DIR / "session_state.json"

DEFAULT_DOCUMENT_TYPES = _base.DEFAULT_DOCUMENT_TYPES
CSV_FIELDS = _base.CSV_FIELDS


def run_santacruz_pipeline(*args, **kwargs):
    """Run the recorder pipeline for Santa Cruz County, AZ."""
    res = _base.run_greenlee_pipeline(*args, **kwargs)

    # Rename output artifacts from greenlee_* to santacruz_* in this county folder.
    csv_path = Path(res.get("csv_path", ""))
    json_path = Path(res.get("json_path", ""))

    ts = Path(csv_path).stem.replace("greenlee_leads_", "") if csv_path else ""
    if ts:
        new_csv = OUTPUT_DIR / f"santacruz_leads_{ts}.csv"
        new_json = OUTPUT_DIR / f"santacruz_leads_{ts}.json"
        if csv_path.exists():
            csv_path.rename(new_csv)
            res["csv_path"] = str(new_csv)
        if json_path.exists():
            json_path.rename(new_json)
            res["json_path"] = str(new_json)

    # County metadata fix
    if isinstance(res.get("summary"), dict):
        res["summary"]["county"] = "Santa Cruz County, AZ"

    # Keep written JSON metadata aligned with Santa Cruz naming/metadata.
    json_out = Path(res.get("json_path", ""))
    if json_out.exists():
        try:
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            if isinstance(payload.get("meta"), dict):
                payload["meta"]["county"] = "Santa Cruz County, AZ"
            json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    return res
