#!/usr/bin/env python3
"""Shared lead document-type defaults for Arizona county interval runners."""

from __future__ import annotations


UNIFIED_LEAD_DOC_TYPES: list[str] = [
    # Pre-foreclosure
    "NOTICE",
    "LIS PENDENS",

    # Distressed / forced sale
    "TAX BILL",
    "TREASURER'S DEED",
    "TREASURERS DEED",
    "TREASURER'S RETURN",
    "TREASURERS RETURN",
    "DIVORCE DECREE",
    "DISSOLUTION",
    "SEPARATION",
    "PROBATE",
    "DEATH CERTIFICATE",
    "PERSONAL REPRESENTATIVE",
    "HEIRSHIP",
    "BANKRUPTCY",

    # Post-foreclosure / REO
    "TRUSTEE'S DEED",
    "TRUSTEES DEED",
    "SHERIFF'S DEED",
    "SHERIFFS DEED",
    "LIEU OF FORECLOSURE",
    "DEED IN LIEU",
    "FORECLOSURE",
]
