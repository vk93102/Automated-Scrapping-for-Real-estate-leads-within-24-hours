#!/usr/bin/env python3
"""Coconino County, AZ pipeline built on shared county-recorder flow."""

from __future__ import annotations

import json
from pathlib import Path

from greenlee import extractor as _base

# County-specific overrides
_base.COUNTY_LABEL = "COCONINO"
_base.COUNTY_DISPLAY = "Coconino"

# Keep outputs isolated in this county folder
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_base.OUTPUT_DIR = OUTPUT_DIR
_base.STORAGE_STATE_PATH = OUTPUT_DIR / "session_state.json"

# Inherit the unified LLM extraction prompt and checks from the shared Greenlee base.

DEFAULT_DOCUMENT_TYPES = _base.DEFAULT_DOCUMENT_TYPES
CSV_FIELDS = _base.CSV_FIELDS


def run_coconino_pipeline(*args, **kwargs):
    """Run the recorder pipeline for Coconino County, AZ."""
    res = _base.run_greenlee_pipeline(*args, **kwargs)

    # Interval runners call with write_output_files=False; the shared pipeline returns empty paths.
    # In that case, skip any filesystem renames/rewrites (runner only needs `records`).
    csv_path_s = str(res.get("csv_path", "") or "").strip()
    json_path_s = str(res.get("json_path", "") or "").strip()
    if not csv_path_s and not json_path_s:
        if isinstance(res.get("summary"), dict):
            res["summary"]["county"] = "Coconino County, AZ"
        return res

    # Rename output artifacts from greenlee_* to coconino_* in this county folder.
    csv_path = Path(csv_path_s) if csv_path_s else None
    json_path = Path(json_path_s) if json_path_s else None

    ts = ""
    if csv_path and csv_path.name:
        ts = csv_path.stem.replace("greenlee_leads_", "")
    elif json_path and json_path.name:
        ts = json_path.stem.replace("greenlee_leads_", "")
    if ts:
        new_csv = OUTPUT_DIR / f"coconino_leads_{ts}.csv"
        new_json = OUTPUT_DIR / f"coconino_leads_{ts}.json"
        if csv_path and csv_path.is_file():
            csv_path.rename(new_csv)
            res["csv_path"] = str(new_csv)
        if json_path and json_path.is_file():
            json_path.rename(new_json)
            res["json_path"] = str(new_json)

    # County metadata fix
    if isinstance(res.get("summary"), dict):
        res["summary"]["county"] = "Coconino County, AZ"

    # Keep written JSON metadata aligned with Coconino naming/metadata.
    json_out_s = str(res.get("json_path", "") or "").strip()
    if json_out_s:
        json_out = Path(json_out_s)
        if not json_out.is_file():
            return res
        try:
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            if isinstance(payload.get("meta"), dict):
                payload["meta"]["county"] = "Coconino County, AZ"
            json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    return res
