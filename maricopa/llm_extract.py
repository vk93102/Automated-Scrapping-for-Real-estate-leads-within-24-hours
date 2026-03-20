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
You are a precise document parser that extracts structured data from county recorder documents (Deed of Trust, Warranty Deed, Release, etc.).

Return ONLY a valid JSON object — no markdown, no explanation, no code fences.
All fields are strings or null. Never return arrays or nested objects.
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

    endpoint_url = (os.environ.get("GROQ_LLM_ENDPOINT_URL") or "").strip()
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
            logger.warning("llm_extract: hosted endpoint failed (%s) — trying direct Groq", exc)

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

def _call_groq(ocr_text: str) -> str:
    """Call the Groq chat-completions endpoint and return the raw content string."""
    from groq import Groq  # lazy import keeps startup fast when groq not needed

    client = Groq(api_key=_GROQ_API_KEY)
    prompt = _USER_PROMPT_TEMPLATE.format(ocr_text=ocr_text)

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

    return ExtractedFields(
        trustor_1_full_name=_str("trustor_1_full_name"),
        trustor_1_first_name=_str("trustor_1_first_name"),
        trustor_1_last_name=_str("trustor_1_last_name"),
        trustor_2_full_name=_str("trustor_2_full_name"),
        trustor_2_first_name=_str("trustor_2_first_name"),
        trustor_2_last_name=_str("trustor_2_last_name"),
        property_address=_str("property_address"),
        address_city=canonicalize_city(city_raw),
        address_state=(state_raw.upper() if state_raw else None),
        address_zip=_str("address_zip"),
        address_unit=_str("address_unit"),
        sale_date=_str("sale_date"),
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
