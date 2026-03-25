#!/usr/bin/env python3
"""Gila County, AZ pipeline built on shared county-recorder flow."""

from __future__ import annotations

import json
from pathlib import Path

from greenlee import extractor as _base

# County-specific overrides
_base.COUNTY_LABEL = "Gila"
_base.COUNTY_DISPLAY = "Gila"

# Keep outputs isolated in this county folder
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_base.OUTPUT_DIR = OUTPUT_DIR
_base.STORAGE_STATE_PATH = OUTPUT_DIR / "session_state.json"

# Inherit the unified LLM extraction prompt and checks from the shared Greenlee base.

DEFAULT_DOCUMENT_TYPES = _base.DEFAULT_DOCUMENT_TYPES
CSV_FIELDS = _base.CSV_FIELDS


def run_gila_pipeline(*args, **kwargs) -> dict:
    """Gila is a thin wrapper around the Greenlee pipeline."""
    # Pop unsupported args before passing to the base implementation.
    kwargs.pop("use_playwright", None)
    res = _base.run_greenlee_pipeline(*args, **kwargs)
    if "summary" in res and isinstance(res["summary"], dict):
        res["summary"]["county"] = "Gila County, AZ"
    return res
