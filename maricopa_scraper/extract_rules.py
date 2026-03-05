from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .cities_az import canonicalize_city


@dataclass(frozen=True)
class ExtractedFields:
    trustor_1_full_name: Optional[str]
    trustor_1_first_name: Optional[str]
    trustor_1_last_name: Optional[str]
    trustor_2_full_name: Optional[str]
    trustor_2_first_name: Optional[str]
    trustor_2_last_name: Optional[str]
    property_address: Optional[str]
    address_city: Optional[str]
    address_state: Optional[str]
    address_zip: Optional[str]
    address_unit: Optional[str]
    sale_date: Optional[str]
    original_principal_balance: Optional[str]


_MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)")
_DATE_RE = re.compile(
    r"\b(?:(?:0?[1-9])|(?:1[0-2]))[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:20\d{2}|\d{2})\b"
)


def _clean_name(s: str) -> Optional[str]:
    s = (s or "").strip(" \t\r\n:;,-")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    # Avoid obviously non-name lines
    if len(s) < 3:
        return None
    if re.search(r"\b(?:PAGE|RECORDED|RECORDING|INSTRUMENT|EXHIBIT)\b", s, flags=re.IGNORECASE):
        return None
    return s


def _split_first_last(full: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not full:
        return (None, None)
    toks = [t for t in re.split(r"\s+", full) if t]
    if len(toks) < 2:
        return (None, None)
    return (toks[0], toks[-1])


def _looks_like_address(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    # Common address-leading patterns.
    if re.match(r"^\d{1,7}\s+\S+", s):
        return True
    if re.search(r"\b(?:AZ|ARIZONA)\b\s+\d{5}\b", s, flags=re.IGNORECASE):
        return True
    return False


def _next_meaningful_block(lines: list[str], start_index: int, *, max_lines: int = 6) -> Optional[str]:
    """Return the next non-empty, non-parenthetical block of lines after start_index.

    Stops early once it looks like the block has ended (blank line after content or an address line).
    """

    out: list[str] = []
    for j in range(start_index + 1, min(len(lines), start_index + 1 + max_lines)):
        s = (lines[j] or "").strip()
        if not s:
            if out:
                break
            continue

        # Skip common parenthetical qualifiers.
        if (s.startswith("(") and s.endswith(")")) or re.search(r"\b(?:as\s+shown|as\s+of)\b", s, flags=re.IGNORECASE):
            continue

        # Stop if we already have content and the next line looks like an address.
        if out and _looks_like_address(s):
            break

        out.append(s)

        # If the next line is blank, we'll exit on the next iteration.

    if not out:
        return None
    block = re.sub(r"\s+", " ", " ".join(out)).strip()
    return block or None


def _parse_trustor_names(block: Optional[str]) -> list[str]:
    if not block:
        return []
    s = re.sub(r"\s+", " ", block).strip(" \t\r\n:;,-")
    if not s:
        return []

    # Prefer splitting two trustors joined by "and" (common on these forms).
    parts = re.split(r"\s+\band\b\s+", s, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        left = (parts[0].split(",", 1)[0]).strip()
        right = (parts[1].split(",", 1)[0]).strip()
        out = [x for x in (left, right) if x]
        return out

    # Otherwise take the first comma-delimited name chunk.
    first = (s.split(",", 1)[0]).strip()
    return [first] if first else []


def extract_fields_rule_based(text: str) -> ExtractedFields:
    """Rule-based extraction from OCR text.

    This is a pragmatic baseline; expect to refine patterns as you sample docs.
    """

    t = (text or "")
    t_norm = re.sub(r"\r", "\n", t)

    # Trustor(s)
    # Many Maricopa NTS PDFs use:
    #   "Name and address of original trustor:" then a parenthetical line, then the trustor name(s).
    lines = t_norm.split("\n")
    trustor_block: Optional[str] = None
    for i, ln in enumerate(lines):
        if re.search(r"\boriginal\s+trustor\b", ln, flags=re.IGNORECASE) or re.search(
            r"\btrustor\(s\)\b|\btrustors\b|\btrustor\b", ln, flags=re.IGNORECASE
        ):
            # Try inline capture after ':' first.
            if ":" in ln:
                after = ln.split(":", 1)[1].strip()
                after_clean = _clean_name(after)
                if after_clean and not re.search(r"\b(?:as\s+shown|as\s+of)\b", after_clean, flags=re.IGNORECASE):
                    trustor_block = after_clean
                    break

            trustor_block = _next_meaningful_block(lines, i, max_lines=8)
            if trustor_block:
                break

    trustor_candidates = _parse_trustor_names(trustor_block)
    trustor_1_full = trustor_candidates[0] if trustor_candidates else None
    trustor_2_full = trustor_candidates[1] if len(trustor_candidates) > 1 else None
    trustor_1_first, trustor_1_last = _split_first_last(trustor_1_full)
    trustor_2_first, trustor_2_last = _split_first_last(trustor_2_full)

    # Property address
    address: Optional[str] = None
    for pat in (
        r"\bPROPERTY\s+ADDRESS\b\s*[:\-]?\s*(.+)",
        r"\bCOMMON\s+ADDRESS\b\s*[:\-]?\s*(.+)",
        r"\bSTREET\s+ADDRESS\b\s*[:\-]?\s*(.+)",
    ):
        m = re.search(pat, t_norm, flags=re.IGNORECASE)
        if m:
            line = m.group(1).split("\n", 1)[0]
            address = line.strip()
            break

    # City/State/Zip heuristics (often on the next line)
    city = state = zip_code = unit = None
    if address:
        # Look at a small window after the match
        idx = t_norm.lower().find(address.lower())
        window = t_norm[idx : idx + 400] if idx >= 0 else ""
        m2 = re.search(r"\b([A-Z][A-Z .'-]+?),\s*([A-Z]{2})\s+(\d{5})\b", window, flags=re.IGNORECASE)
        if m2:
            city = canonicalize_city(m2.group(1).strip())
            state = m2.group(2).upper()
            zip_code = m2.group(3)
        munit = re.search(r"\b(?:APT|APARTMENT|UNIT|STE|SUITE|#)\s*([A-Z0-9-]+)\b", address, flags=re.IGNORECASE)
        if munit:
            unit = munit.group(1)

    # Sale date
    sale_date: Optional[str] = None
    mdate = re.search(r"\b(?:SALE\s+DATE|DATE\s+OF\s+SALE)\b\s*[:\-]?\s*([0-9/\-]+)", t_norm, flags=re.IGNORECASE)
    if mdate:
        sale_date = mdate.group(1).strip()
    else:
        mdate2 = _DATE_RE.search(t_norm)
        if mdate2:
            sale_date = mdate2.group(0)

    # Original principal balance
    opb: Optional[str] = None
    m_bal = re.search(
        r"\b(?:ORIGINAL\s+PRINCIPAL\s+BALANCE|ORIGINAL\s+BALANCE|PRINCIPAL\s+BALANCE)\b.*?\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)",
        t_norm,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m_bal:
        opb = m_bal.group(1)
    else:
        m_money = _MONEY_RE.search(t_norm)
        if m_money:
            opb = m_money.group(1)

    return ExtractedFields(
        trustor_1_full_name=trustor_1_full,
        trustor_1_first_name=trustor_1_first,
        trustor_1_last_name=trustor_1_last,
        trustor_2_full_name=trustor_2_full,
        trustor_2_first_name=trustor_2_first,
        trustor_2_last_name=trustor_2_last,
        property_address=address,
        address_city=canonicalize_city(city),
        address_state=state,
        address_zip=zip_code,
        address_unit=unit,
        sale_date=sale_date,
        original_principal_balance=(opb.replace(",", "") if opb else None),
    )
