from __future__ import annotations

import base64
import io
import json
import os
from typing import Iterable

import requests


def _groq_key() -> str:
    return (os.environ.get("GROQ_API_KEY") or os.environ.get("LLAMA_API_KEY") or "").strip()


def _groq_model() -> str:
    # Try newer vision model first, fall back to alternative if needed
    model_env = (os.environ.get("GROQ_OCR_MODEL") or "").strip()
    if model_env:
        return model_env
    # Default fallback chain: newer vision models
    # Note: Groq's vision model availability changes; if unavailable, system falls back to metadata
    return "llama-2-vision-preview"


def _timeout_s() -> float:
    raw = (os.environ.get("GROQ_OCR_TIMEOUT_S") or "90").strip()
    try:
        return max(10.0, float(raw))
    except Exception:
        return 90.0


def _max_pages() -> int:
    raw = (os.environ.get("GROQ_OCR_MAX_PAGES") or "8").strip()
    try:
        return max(1, int(raw))
    except Exception:
        return 8


def ocr_pdf_bytes_via_groq(pdf_bytes: bytes, *, max_pages: int | None = None, dpi: int = 220) -> str:
    """Render PDF pages and OCR via Groq vision model.

    This avoids Tesseract completely.
    """
    if not pdf_bytes:
        return ""

    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(pdf_bytes, dpi=dpi)
    limit = _max_pages() if max_pages is None else max(1, int(max_pages))
    pages = pages[:limit]

    ocr_chunks: list[str] = []
    for i, img in enumerate(pages, start=1):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        page_text = ocr_image_bytes_via_groq(buf.getvalue(), page_index=i)
        if page_text:
            ocr_chunks.append(page_text)

    return "\n\n".join(ocr_chunks).strip()


def ocr_image_bytes_via_groq(image_bytes: bytes, *, page_index: int = 1) -> str:
    key = _groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured for Groq OCR")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    system_prompt = (
         "Extract real estate document fields with strict quality rules. Return JSON with keys: "
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
    )
    user_text = (
        f"OCR this page (page {page_index}). Return full text in reading order. "
        "Preserve dates, amounts, names, addresses, and punctuation where visible."
    )

    payload = {
        "model": _groq_model(),
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_timeout_s(),
    )
    
    # Debug: log response if error
    if resp.status_code >= 400:
        try:
            err_data = resp.json()
            import sys
            print(f"Groq OCR error ({resp.status_code}): {err_data}", file=sys.stderr)
        except Exception:
            print(f"Groq OCR HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
    
    resp.raise_for_status()
    data = resp.json()
    try:
        return str(data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        raise RuntimeError(f"Unexpected Groq OCR response shape: {exc}")
