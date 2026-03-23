"""LLM-based field extraction using Groq (llama-3.1-8b-instant).

Sends OCR text to the Groq API and asks the model to extract structured
entity fields that map 1-to-1 to the ``properties`` DB table.

Falls back to rule-based extraction automatically if:
- GROQ_API_KEY env var is missing
- The API call fails
- The response cannot be parsed as valid JSON
"""
from __future__ import annotations

import json
import logging
import os
import re
import base64
import time
from typing import Optional

import requests

from .extract_rules import ExtractedFields, extract_fields_rule_based
from .cities_az import canonicalize_city

logger = logging.getLogger(__name__)

_GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("LLAMA_API_KEY")
_MODEL = "llama-3.1-8b-instant"

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
Extract real estate document fields with strict quality rules. Return JSON with keys: "
        "trustor, trustee, beneficiary, principalAmount, propertyAddress, grantors, grantees. "
        "STRICT RULES FOR REAL PERSON NAMES: "
        "1. Extract ONLY the primary borrower/trustor name (single person or business entity). "
        "2. If multiple names appear, select ONLY the first/primary one—ignore 'and', 'or', co-borrowers. "
        "3. Remove ALL descriptive text after the name: ignore 'as trustee for', 'dba', 'et al', 'a California LLC', etc. "
        "4. Only keep business suffixes if they are part of the true entity name: LLC, INC, CORP, COMPANY, TRUST, BANK, ASSOCIATION. "
        "5. Do NOT return generic titles, roles, or descriptors. Real name only. "
        "6. If multiple completely different entities, return first primary one only. "
        "7. If there is no meaningfull result is present in the text, you can kept as empty there or not found record there"
        "STRICT RULES FOR PROPERTY ADDRESS: "
        "1. Extract ONLY the actual property street address (street number + street name + optional city/state/zip). "
        "2. Must be a real US street address format: e.g., '123 Main St, Phoenix, AZ 85001'. "
        "3. Do NOT include: legal descriptions, parcel IDs, 'Lot X Block Y', subdivision names, recording boilerplate. "
        "4. If multiple addresses appear, use the specific property address (not mailing addresses). "
        "5. Arizona cities only (Maricopa, Phoenix, Tucson, Glendale, Chandler, Gilbert, etc.)—no foreign locations. "
        "6. If there is no meaningfull result is present in the text, you can kept as empty there or not found record there"

        "DOLLAR AMOUNTS & ARRAYS: "
        "1. principalAmount must be dollar format: '$123,456.78' when present; only if >= $1,000. "
        "2. grantors/grantees are arrays of real names (keep up to 2 each if multiple). "
        "3. If unknown or unclear, return empty string or empty array—DO NOT GUESS. "
        "4. If there is no meaningfull result is present in the text, you can kept as empty there or not found record there"
        "OUTPUT: Return valid JSON only. Must extract only REAL data, never invent values."
"""

_FEW_SHOT_EXAMPLES = """\
Example 1
Input snippet:
    Name and address of original trustor:
    JOHN A DOE AND JANE B DOE
    1234 E CAMELBACK RD PHOENIX AZ 85016
    Original Principal Amount: $425,000.00
    Dated: 02/14/2026

Output JSON:
    {
        "trustor_1_full_name": "JOHN A DOE",
        "trustor_1_first_name": "JOHN",
        "trustor_1_last_name": "DOE",
        "trustor_2_full_name": "JANE B DOE",
        "trustor_2_first_name": "JANE",
        "trustor_2_last_name": "DOE",
        "property_address": "1234 E CAMELBACK RD",
        "address_city": "PHOENIX",
        "address_state": "AZ",
        "address_zip": "85016",
        "address_unit": null,
        "sale_date": "02/14/2026",
        "original_principal_balance": "425000.00"
    }

Example 2
Input snippet:
    Trustor: Maria L Gonzales
    Property Address: 901 W Elm St Apt 4B, Mesa, Arizona 85201
    Loan Amount 315000

Output JSON:
    {
        "trustor_1_full_name": "Maria L Gonzales",
        "trustor_1_first_name": "Maria",
        "trustor_1_last_name": "Gonzales",
        "trustor_2_full_name": null,
        "trustor_2_first_name": null,
        "trustor_2_last_name": null,
        "property_address": "901 W Elm St",
        "address_city": "Mesa",
        "address_state": "AZ",
        "address_zip": "85201",
        "address_unit": "4B",
        "sale_date": null,
        "original_principal_balance": "315000"
    }
"""

_USER_PROMPT_TEMPLATE = """\
Extract the following fields from the OCR text of this county recorder document.
Return a single flat JSON object with exactly these keys (use null for missing/unknown values):

  trustor_1_full_name     – Full name of the first trustor/grantor/borrower
  trustor_1_first_name    – First name only of trustor 1
  trustor_1_last_name     – Last name only of trustor 1
  trustor_2_full_name     – Full name of second trustor (if any), else null
  trustor_2_first_name    – First name only of trustor 2
  trustor_2_last_name     – Last name only of trustor 2
  property_address        – Street address of the property (number + street, no city/state/zip)
  address_city            – City name only
  address_state           – 2-letter US state code (e.g. AZ)
  address_zip             – 5-digit ZIP code
  address_unit            – Apartment / unit / suite number (e.g. "4B"), else null
  sale_date               – Date of sale or loan origination in MM/DD/YYYY format, else null
  original_principal_balance – Loan amount as a decimal number string (no $ sign, no commas), else null

Few-shot learning examples:
{few_shot_examples}

Document text:
---
{ocr_text}
---
"""

_MAX_OCR_CHARS = 6_000   # keep well within 8k context limit


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_fields_llm(
    ocr_text: str,
    *,
    fallback_to_rule_based: bool = True,
) -> ExtractedFields:
    """Extract structured fields from ``ocr_text`` using the Groq LLM.

    If the LLM call fails or returns unparseable JSON, and ``fallback_to_rule_based``
    is True, the function falls back to the regex-based extractor so the pipeline
    never blocks.
    """
    if not ocr_text or not ocr_text.strip():
        logger.debug("llm_extract: empty OCR text — returning empty fields")
        return _empty_fields()

    require_endpoint = str(os.environ.get("GROQ_LLM_ENDPOINT_REQUIRED", "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    endpoint_url = (os.environ.get("GROQ_LLM_ENDPOINT_URL") or "").strip()
    if require_endpoint and not endpoint_url:
        raise RuntimeError("GROQ_LLM_ENDPOINT_REQUIRED=1 but GROQ_LLM_ENDPOINT_URL is not configured")

    if endpoint_url:
        try:
            fields = _call_hosted_llm_endpoint(
                endpoint_url=endpoint_url,
                ocr_text=ocr_text,
                fallback_to_rule_based=fallback_to_rule_based,
            )
            logger.debug("llm_extract: successfully extracted fields via hosted endpoint")
            return fields
        except Exception as exc:
            if require_endpoint:
                raise RuntimeError(f"Hosted LLM endpoint call failed: {exc}") from exc
            logger.warning("llm_extract: hosted endpoint failed (%s) — trying direct Groq", exc)

    if require_endpoint:
        raise RuntimeError("Hosted LLM endpoint is required; no fallback allowed")

    return extract_fields_llm_direct(ocr_text, fallback_to_rule_based=fallback_to_rule_based)


def extract_fields_llm_direct(
    ocr_text: str,
    *,
    fallback_to_rule_based: bool = True,
) -> ExtractedFields:
    """Extract fields by calling Groq directly (no hosted endpoint hop)."""

    if not _GROQ_API_KEY:
        logger.warning("llm_extract: GROQ_API_KEY not set — falling back to rule-based")
        return extract_fields_rule_based(ocr_text)

    # Truncate to avoid exceeding model context window.
    truncated = ocr_text[:_MAX_OCR_CHARS]
    if len(ocr_text) > _MAX_OCR_CHARS:
        logger.debug("llm_extract: OCR text truncated from %d to %d chars", len(ocr_text), _MAX_OCR_CHARS)

    try:
        raw = _call_groq(truncated)
        fields = _parse_response(raw)
        logger.debug("llm_extract: successfully extracted fields for document")
        return fields
    except Exception as exc:
        logger.warning("llm_extract: LLM extraction failed (%s)", exc)
        if fallback_to_rule_based:
            logger.info("llm_extract: falling back to rule-based extraction")
            return extract_fields_rule_based(ocr_text)
        return _empty_fields()


def extract_fields_llm_document_endpoint(
    pdf_bytes: bytes,
    *,
    fallback_to_rule_based: bool = True,
    recording_number: str = "",
) -> tuple[ExtractedFields, str]:
    """Call hosted document endpoint (PDF -> OCR+LLM on server side).

    Returns tuple: (fields, ocr_text_used_by_server).
    """
    endpoint_url = (os.environ.get("GROQ_LLM_DOCUMENT_ENDPOINT_URL") or "").strip()
    if not endpoint_url:
        base = (os.environ.get("GROQ_LLM_ENDPOINT_URL") or "").strip()
        if base:
            endpoint_url = base.replace("/api/v1/llm/extract", "/api/v1/llm/extract-document")

    if not endpoint_url:
        raise ValueError("GROQ_LLM_DOCUMENT_ENDPOINT_URL not configured")

    timeout_s = float(os.environ.get("GROQ_LLM_ENDPOINT_TIMEOUT_S", "60"))
    payload = {
        "pdf_base64": base64.b64encode(pdf_bytes or b"").decode("ascii"),
        "fallback_to_rule_based": bool(fallback_to_rule_based),
        "recording_number": str(recording_number or ""),
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_token = (os.environ.get("API_TOKEN") or "").strip()
    if api_token:
        headers["X-API-Token"] = api_token

    resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Hosted document endpoint returned non-object JSON")

    fields_dict = data.get("fields", data)
    if not isinstance(fields_dict, dict):
        raise ValueError("Hosted document endpoint response missing object field payload")

    mapped = _map_fields_dict(fields_dict)
    ocr_text = str(data.get("ocr_text") or "")
    return mapped, ocr_text


def _call_hosted_llm_endpoint(
    *,
    endpoint_url: str,
    ocr_text: str,
    fallback_to_rule_based: bool,
) -> ExtractedFields:
    """Call a hosted extraction endpoint and map response JSON to ``ExtractedFields``."""
    timeout_s = float(os.environ.get("GROQ_LLM_ENDPOINT_TIMEOUT_S", "60"))
    payload = {
        "ocr_text": ocr_text,
        "fallback_to_rule_based": bool(fallback_to_rule_based),
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_token = (os.environ.get("API_TOKEN") or "").strip()
    if api_token:
        headers["X-API-Token"] = api_token

    resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Hosted endpoint returned non-object JSON")

    fields = data.get("fields", data)
    if not isinstance(fields, dict):
        raise ValueError("Hosted endpoint response missing object field payload")

    def _str_or_none(key: str) -> Optional[str]:
        val = fields.get(key)
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    city_raw = _str_or_none("address_city")
    state_raw = _str_or_none("address_state")
    opb_raw = _str_or_none("original_principal_balance")

    if opb_raw:
        opb_raw = re.sub(r"[$,]", "", opb_raw).strip()
        if not re.match(r"^\d+(\.\d+)?$", opb_raw):
            opb_raw = None

    return ExtractedFields(
        trustor_1_full_name=_str_or_none("trustor_1_full_name"),
        trustor_1_first_name=_str_or_none("trustor_1_first_name"),
        trustor_1_last_name=_str_or_none("trustor_1_last_name"),
        trustor_2_full_name=_str_or_none("trustor_2_full_name"),
        trustor_2_first_name=_str_or_none("trustor_2_first_name"),
        trustor_2_last_name=_str_or_none("trustor_2_last_name"),
        property_address=_str_or_none("property_address"),
        address_city=canonicalize_city(city_raw),
        address_state=(state_raw.upper() if state_raw else None),
        address_zip=_str_or_none("address_zip"),
        address_unit=_str_or_none("address_unit"),
        sale_date=_str_or_none("sale_date"),
        original_principal_balance=opb_raw,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_groq(ocr_text: str, max_retries: int = 3) -> str:
    """Call the Groq chat-completions endpoint with exponential backoff retry.
    
    Handles 429 (rate limit) errors by waiting and retrying.
    """
    from groq import Groq, APIStatusError  # lazy import keeps startup fast when groq not needed

    client = Groq(api_key=_GROQ_API_KEY)
    prompt = _USER_PROMPT_TEMPLATE.format(
        few_shot_examples=_FEW_SHOT_EXAMPLES,
        ocr_text=ocr_text,
    )

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=512,
            )
            return response.choices[0].message.content or ""
        except APIStatusError as e:
            if e.status_code == 429:  # Rate limit
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s exponential backoff
                    logger.info(f"Rate limited (429). Retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
            raise


def _parse_response(raw: str) -> ExtractedFields:
    """Parse the LLM JSON response into an ``ExtractedFields`` instance."""
    # Strip potential markdown code-fence wrappers the model may still emit.
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data: dict = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first {...} block in the response.
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not m:
            raise ValueError(f"No JSON object found in LLM response: {raw[:200]!r}")
        data = json.loads(m.group(0))

    def _str(key: str) -> Optional[str]:
        val = data.get(key)
        if val is None or str(val).strip().lower() in ("null", "none", "n/a", ""):
            return None
        return str(val).strip()

    city_raw = _str("address_city")
    state_raw = _str("address_state")
    opb_raw = _str("original_principal_balance")

    # Normalise principal balance — strip any stray $ or commas the model may include.
    if opb_raw:
        opb_raw = re.sub(r"[$,]", "", opb_raw).strip()
        if not re.match(r"^\d+(\.\d+)?$", opb_raw):
            opb_raw = None

    return _map_fields_dict(
        {
            "trustor_1_full_name": _str("trustor_1_full_name"),
            "trustor_1_first_name": _str("trustor_1_first_name"),
            "trustor_1_last_name": _str("trustor_1_last_name"),
            "trustor_2_full_name": _str("trustor_2_full_name"),
            "trustor_2_first_name": _str("trustor_2_first_name"),
            "trustor_2_last_name": _str("trustor_2_last_name"),
            "property_address": _str("property_address"),
            "address_city": canonicalize_city(city_raw),
            "address_state": (state_raw.upper() if state_raw else None),
            "address_zip": _str("address_zip"),
            "address_unit": _str("address_unit"),
            "sale_date": _str("sale_date"),
            "original_principal_balance": opb_raw,
        }
    )


def _map_fields_dict(fields: dict) -> ExtractedFields:
    def _str_or_none(key: str) -> Optional[str]:
        val = fields.get(key)
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    city_raw = _str_or_none("address_city")
    state_raw = _str_or_none("address_state")
    opb_raw = _str_or_none("original_principal_balance")

    if opb_raw:
        opb_raw = re.sub(r"[$,]", "", opb_raw).strip()
        if not re.match(r"^\d+(\.\d+)?$", opb_raw):
            opb_raw = None

    return ExtractedFields(
        trustor_1_full_name=_str_or_none("trustor_1_full_name"),
        trustor_1_first_name=_str_or_none("trustor_1_first_name"),
        trustor_1_last_name=_str_or_none("trustor_1_last_name"),
        trustor_2_full_name=_str_or_none("trustor_2_full_name"),
        trustor_2_first_name=_str_or_none("trustor_2_first_name"),
        trustor_2_last_name=_str_or_none("trustor_2_last_name"),
        property_address=_str_or_none("property_address"),
        address_city=canonicalize_city(city_raw),
        address_state=(state_raw.upper() if state_raw else None),
        address_zip=_str_or_none("address_zip"),
        address_unit=_str_or_none("address_unit"),
        sale_date=_str_or_none("sale_date"),
        original_principal_balance=opb_raw,
    )


def _empty_fields() -> ExtractedFields:
    return ExtractedFields(
        trustor_1_full_name=None,
        trustor_1_first_name=None,
        trustor_1_last_name=None,
        trustor_2_full_name=None,
        trustor_2_first_name=None,
        trustor_2_last_name=None,
        property_address=None,
        address_city=None,
        address_state=None,
        address_zip=None,
        address_unit=None,
        sale_date=None,
        original_principal_balance=None,
    )
