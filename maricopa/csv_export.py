from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional


BASE_CSV_HEADERS: list[str] = [
    "Document URL",
    "Trustor 1 Full Name",
    "Trustor 1 First Name",
    "Trustor 1 Last Name",
    "Trustor 2 Full Name",
    "Trustor 2 First Name",
    "Trustor 2 Last Name",
    "Address City",
    "Address State",
    "Address Zip",
    "Property address",
    "Sale Date",
    "Original Principal Balance",
    "Address Unit",
]

META_CSV_HEADERS: list[str] = [
    "Recording Number",
    "Recording Date",
    "Document Type",
    "Page Amount",
]


def csv_headers(*, include_meta: bool) -> list[str]:
    return BASE_CSV_HEADERS + (META_CSV_HEADERS if include_meta else [])


def _get(row: dict[str, Any], *keys: str) -> Optional[str]:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return str(row[k])
    return None


def to_csv_rows(rows: Iterable[dict[str, Any]], *, include_meta: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        doc_codes = r.get("documentCodes") or []
        doc_type = None
        if isinstance(doc_codes, list) and doc_codes:
            doc_type = str(doc_codes[0])

        base: dict[str, Any] = {
            "Document URL": _get(r, "document_url", "documentUrl"),
                "Trustor 1 Full Name": _get(r, "trustor_1_full_name"),
                "Trustor 1 First Name": _get(r, "trustor_1_first_name"),
                "Trustor 1 Last Name": _get(r, "trustor_1_last_name"),
                "Trustor 2 Full Name": _get(r, "trustor_2_full_name"),
                "Trustor 2 First Name": _get(r, "trustor_2_first_name"),
                "Trustor 2 Last Name": _get(r, "trustor_2_last_name"),
                "Address City": _get(r, "address_city"),
                "Address State": _get(r, "address_state"),
                "Address Zip": _get(r, "address_zip"),
                "Property address": _get(r, "property_address"),
                "Sale Date": _get(r, "sale_date"),
                "Original Principal Balance": _get(r, "original_principal_balance"),
                "Address Unit": _get(r, "address_unit"),
            }

        if include_meta:
            base.update(
                {
                    "Recording Number": _get(r, "recordingNumber"),
                    "Recording Date": _get(r, "recordingDate"),
                    "Document Type": doc_type,
                    "Page Amount": r.get("pageAmount"),
                }
            )

        out.append(base)
    return out


def write_csv(path: str, rows: Iterable[dict[str, Any]], *, include_meta: bool = False) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=csv_headers(include_meta=include_meta))
        w.writeheader()
        for r in to_csv_rows(rows, include_meta=include_meta):
            w.writerow(r)
    return p


def write_dated_csv(
    out_dir: str,
    rows: Iterable[dict[str, Any]],
    *,
    prefix: str = "new_records",
    include_meta: bool = False,
) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{prefix}_{date.today().isoformat()}.csv"
    return write_csv(str(p), rows, include_meta=include_meta)


def filter_by_cities(rows: Iterable[dict[str, Any]], cities: list[str]) -> list[dict[str, Any]]:
    targets = {c.strip().casefold() for c in (cities or []) if (c or "").strip()}
    if not targets:
        return list(rows)

    out: list[dict[str, Any]] = []
    for r in rows:
        c = (r.get("address_city") or "").strip().casefold()
        if c in targets:
            out.append(r)
    return out


def render_csv_string(rows: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO()
    # Server renders the base sheet by default.
    w = csv.DictWriter(buf, fieldnames=csv_headers(include_meta=False))
    w.writeheader()
    for r in to_csv_rows(rows, include_meta=False):
        w.writerow(r)
    return buf.getvalue()
