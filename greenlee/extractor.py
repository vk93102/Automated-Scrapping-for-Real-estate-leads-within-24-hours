#!/usr/bin/env python3
"""Greenlee County, AZ — Real Estate Lead Scraper & Enrichment Pipeline."""

from __future__ import annotations

import csv
import base64
import io
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageFile, ImageFilter, ImageEnhance

# Allow PIL to open truncated/incomplete JPEG files from the server
ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import pytesseract
    # Explicitly set tesseract binary path (Homebrew on macOS)
    for _tess_bin in [
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]:
        if Path(_tess_bin).exists():
            pytesseract.pytesseract.tesseract_cmd = _tess_bin
            break
except Exception:  # pragma: no cover
    pytesseract = None

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_OK = True
except Exception:  # pragma: no cover
    _PLAYWRIGHT_OK = False


BASE_URL = "https://www.thecountyrecorder.com"
SEARCH_URL = f"{BASE_URL}/Search.aspx"
RESULTS_URL = f"{BASE_URL}/Results.aspx"
DOCUMENT_URL = f"{BASE_URL}/Document.aspx"
IMAGE_HANDLER_URL = f"{BASE_URL}/ImageHandler.ashx"
COUNTY_LABEL = "GREENLEE"
COUNTY_DISPLAY = "Greenlee"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STORAGE_STATE_PATH = OUTPUT_DIR / "session_state.json"
ROOT_DIR = Path(__file__).resolve().parent.parent
_GROQ_MODEL_CACHE: list[str] | None = None

DEFAULT_DOCUMENT_TYPES = [
    "NOTICE OF DEFAULT",
    "NOTICE OF TRUSTEE SALE",
    "LIS PENDENS",
    "DEED IN LIEU",
    "TREASURERS DEED",
    "NOTICE OF REINSTATEMENT",
]

CSV_FIELDS = [
    "documentId",
    "documentUrls",
    "recordingNumber",
    "recordingDate",
    "documentType",
    "grantors",
    "grantees",
    "trustor",
    "trustee",
    "beneficiary",
    "principalAmount",
    "propertyAddress",
    "detailUrl",
    "imageUrls",
    "links",
    "ocrMethod",
    "ocrChars",
    "usedGroq",
    "groqModel",
    "groqError",
    "sourceCounty",
    "analysisError",
    "manualReview",
    "manualReviewReasons",
    "manualReviewSummary",
    "manualReviewContext",
]


def _compute_manual_review(record: dict, merged_text: str = "") -> tuple[bool, str, str, str]:
    not_found_fields: list[str] = []
    # Lead-critical fields for review: trustor, principalAmount, propertyAddress.
    for k in ["trustor", "principalAmount", "propertyAddress"]:
        if str(record.get(k, "") or "").strip() == "NOT_FOUND":
            not_found_fields.append(k)

    groq_err = str(record.get("groqError", "") or "").strip()

    # Only trigger manual review when key extraction is missing/failed.
    # (Santa Cruz request: focus on NOT_FOUND records; Groq errors also require review.)
    manual = bool(not_found_fields or groq_err)

    reasons: list[str] = []
    if manual:
        if not_found_fields:
            reasons.append("NOT_FOUND:" + ",".join(not_found_fields))
        if groq_err:
            reasons.append("GROQ_ERROR")
        # analysisError is often a non-fatal note (e.g., image-blocked counties).
        # Include it as context only when we're already flagging manual review.
        if str(record.get("analysisError", "") or "").strip():
            reasons.append("ANALYSIS_ERROR")
    reasons_s = " | ".join(reasons)

    summary = ""
    context = ""
    if manual:
        trustor = str(record.get("trustor", "") or "").strip()
        beneficiary = str(record.get("beneficiary", "") or "").strip()
        addr = str(record.get("propertyAddress", "") or "").strip()
        amt = str(record.get("principalAmount", "") or "").strip()
        summary = f"trustor={trustor}; beneficiary={beneficiary}; address={addr}; principal={amt}"

        snippet = re.sub(r"\s+", " ", str(merged_text or "")).strip()
        context = snippet[:800]

    return manual, reasons_s, summary, context

COUNTY_LLM_SYSTEM_PROMPT = """
You are an expert AI assistant and certified prompt engineer specializing in structured data extraction from real estate legal documents — specifically Deeds of Trust, Grant Deeds, Warranty Deeds, Quitclaim Deeds, and related instruments recorded in Cochise County, AZ. You have deep knowledge of Arizona real property law terminology, OCR artifact patterns, and legal document formatting conventions.

Your singular objective is to receive raw OCR text from a scanned legal document and return a single, valid JSON object conforming exactly to the schema and validation rules defined below. Every instruction in this prompt is mandatory. There are no optional steps.

════════════════════════════════════════════════
SECTION 1 — END-TO-END EXTRACTION PROCESS
════════════════════════════════════════════════

You MUST execute the following steps in order before producing any output:

STEP 1 — FULL DOCUMENT READ
    Read the entire OCR text from start to finish without skipping. Do not anchor to the first match you find. Legal documents frequently repeat party names with variations; you must identify the authoritative instance.

STEP 2 — DOCUMENT TYPE CLASSIFICATION
    Silently classify the document type (e.g., Deed of Trust, Warranty Deed, Assignment of Deed of Trust, Notice of Trustee's Sale, etc.). This classification affects which fields are likely present and where they appear.

STEP 3 — ENTITY IDENTIFICATION
    Locate the following entities using context-aware scanning:
    - Trustor / Grantor / Borrower (the party conveying or pledging the property)
    - Trustee (neutral third party holding title, often a title company)
    - Beneficiary / Grantee / Lender (the party receiving the conveyance or to whom debt is owed)
    - Principal/Loan Amount (the original face value of the obligation)
    - Situs / Property Address (the physical location of the real property)
    - All Grantors (every party in the "from" position)
    - All Grantees (every party in the "to" position)

STEP 4 — FIELD-LEVEL VALIDATION
    For each extracted value, apply ALL validation rules from Section 3 before writing output. Mentally check each rule like a checklist. Do not skip any rule.

STEP 5 — JSON CONSTRUCTION
    Assemble the final JSON object. Perform a final self-review: verify all keys are present, all values conform to their rules, and no markdown or prose surrounds the JSON.

════════════════════════════════════════════════
SECTION 2 — OUTPUT SCHEMA (STRICT)
════════════════════════════════════════════════

You MUST return a JSON object containing EXACTLY these keys — no more, no fewer:

{
  "trustor":          <string>,
  "trustee":          <string>,
  "beneficiary":      <string>,
  "principalAmount":  <string>,
  "propertyAddress":  <string>,
  "grantors":         <array of strings>,
  "grantees":         <array of strings>,
  "confidenceNote":   <string>
}

Key definitions:
- "trustor"         → The primary borrower or person/entity who conveyed or pledged the property.
- "trustee"         → The neutral third party (often a title/escrow company) holding legal title during the loan term.
- "beneficiary"     → The lender or entity to whom the debt is owed; holds the beneficial interest.
- "principalAmount" → The original face amount of the loan or obligation secured by the instrument.
- "propertyAddress" → The physical street/situs address of the real property in Cochise County, AZ.
- "grantors"        → All parties in the "grantor" / "from" position across the entire document.
- "grantees"        → All parties in the "grantee" / "to" position across the entire document.
- "confidenceNote"  → A machine-readable audit string (see Section 3, Rule 1 for format).

════════════════════════════════════════════════
SECTION 3 — VALIDATION RULES (THE "CHECKBAR")
════════════════════════════════════════════════

Every rule below is MANDATORY. Apply each one to every field before finalizing output.

──────────────────────────────────────────────
RULE 1 — MISSING VALUE HANDLING
──────────────────────────────────────────────
    • If a string field's value cannot be confidently found in the document, set it to exactly: "NOT_FOUND"
    • For array fields (grantors, grantees), use an empty array: []
    • The "confidenceNote" field:
        - MUST list every string field set to "NOT_FOUND" in this exact format:
          "NOT_FOUND:<field1>,<field2>"  (e.g., "NOT_FOUND:trustee,principalAmount")
        - If ALL fields were found, set confidenceNote to: ""
        - If ONLY arrays were empty, still set confidenceNote to "" (arrays are not tracked here)
        - Do NOT add narrative text to confidenceNote — it is machine-parsed.

──────────────────────────────────────────────
RULE 2 — NAME FIELD CONSTRAINTS
  Applies to: trustor, trustee, beneficiary
──────────────────────────────────────────────

    ╔══════════════════════════════════════════════════════╗
    ║  CORE PRINCIPLE: Each individual name entry MUST     ║
    ║  never exceed 5 words. When multiple parties exist,  ║
    ║  separate them with a comma — do NOT join them as    ║
    ║  one run-on string.                                  ║
    ╚══════════════════════════════════════════════════════╝

    a) SINGLE PARTY — WORD COUNT:
        - The extracted name MUST be between 1 and 5 words (inclusive).
        - If the cleaned name still exceeds 5 words, truncate to the first 5 words.
        - Count only meaningful name tokens — do NOT count stripped boilerplate words toward the limit.

        Correct:   "JOHN A DOE"                        (3 words ✓)
        Correct:   "FIDELITY NATIONAL TITLE AGENCY INC" (5 words ✓)
        Incorrect: "FIDELITY NATIONAL TITLE AGENCY OF ARIZONA INC" (7 words — truncate to first 5)
                    → "FIDELITY NATIONAL TITLE AGENCY OF"

    b) MULTIPLE PARTIES — COMMA SEPARATION:
        - If the source text contains multiple parties joined by "AND", "OR", "&", or listed
          sequentially, extract EACH party as a separate name token, clean it individually
          (apply Rule 2c and the 5-word limit independently to each), then join all tokens
          with ", " (comma + space) as a single string value.
        - There is NO cap on the number of parties that may appear in the comma-separated string —
          capture all of them.
        - Each individual name segment between commas MUST independently satisfy the 5-word limit.

        Source:  "JOHN A DOE AND JANE B DOE, HUSBAND AND WIFE"
        Step 1 — Split on AND:        ["JOHN A DOE", "JANE B DOE, HUSBAND AND WIFE"]
        Step 2 — Strip boilerplate:   ["JOHN A DOE", "JANE B DOE"]
        Step 3 — Check word counts:   3 words ✓, 3 words ✓
        Step 4 — Join:                "JOHN A DOE, JANE B DOE"
        Final value → "JOHN A DOE, JANE B DOE"

        Source:  "ROBERT T KING AND MARY K KING AND SAMUEL P KING"
        Result → "ROBERT T KING, MARY K KING, SAMUEL P KING"

    c) STRIP ALL OF THE FOLLOWING — these are NEVER part of a valid name value:
        Relationship/marital descriptors   → "husband and wife", "a married couple", "a single man/woman",
                                             "an unmarried man/woman", "a widower/widow", "joint tenants",
                                             "tenants in common", "community property"
        Role/capacity suffixes             → "as trustee", "as beneficiary", "as nominee", "as agent",
                                             "as personal representative", "as executor"
        Legal entity boilerplate           → "a corporation", "a limited liability company", "an Arizona LLC",
                                             "an Arizona corporation", "a California corporation"
        Succession language                → "its successors and assigns", "and their successors",
                                             "and assigns"
        Recording artifacts                → Document headers, page numbers, recording stamps,
                                             instrument numbers mixed into name text

    d) ALLOWED CORPORATE SUFFIXES — these ARE part of a valid legal entity name and count toward the 5-word limit:
        Allowed: "LLC", "INC", "CORP", "LP", "LLP", "NA", "FSB", "N.A.", "PLC", "PC"
        Example valid value: "WELLS FARGO BANK NA"  (4 words ✓)

    e) CAPITALIZATION: Preserve the casing exactly as it appears in the source document. Do not normalize to upper or lower case.

    f) WORD COUNT ENFORCEMENT SUMMARY TABLE:

        Scenario                                           Action
        ─────────────────────────────────────────────────────────────────────
        Single party, ≤5 words after stripping          → Use as-is
        Single party, >5 words after stripping          → Truncate to first 5 words
        Multiple parties, each ≤5 words after stripping → Join with ", "
        Multiple parties, one exceeds 5 words           → Truncate that individual entry to 5 words, then join
        No party found                                  → "NOT_FOUND"

──────────────────────────────────────────────
RULE 3 — principalAmount CONSTRAINTS
──────────────────────────────────────────────
    a) FORMAT: The value MUST be a string containing only:
        - ASCII digits (0–9)
        - An optional single decimal point (.)
        No other characters are permitted under any circumstances.

    b) PROHIBITED CHARACTERS (absolute exclusions):
        - Dollar sign: $
        - Comma: ,
        - Currency codes: USD, USD$, etc.
        - Parentheses, spaces, hyphens, or any letter

    c) MINIMUM VALUE: The numeric value represented must be ≥ 1000.00. If the extracted amount is less than 1000, set to "NOT_FOUND".

    d) WRITTEN-OUT AMOUNTS: If the amount appears in prose form (e.g., "One Hundred Fifty Thousand Dollars"), attempt to convert it to numeric form. If conversion is ambiguous or impossible, use "NOT_FOUND".

    e) MULTIPLE AMOUNTS: Documents may state both a loan amount and a total secured amount. Use the ORIGINAL PRINCIPAL / LOAN AMOUNT, not a maximum lien or future advance amount.

    f) VALID EXAMPLES:
        "$1,250,000.50"      → "1250000.50"
        "$85,000"            → "85000"
        "Two Hundred Thousand Dollars ($200,000.00)" → "200000.00"
        "Nine Hundred Dollars" → "NOT_FOUND"  (below minimum)
        "$500"               → "NOT_FOUND"  (below minimum)

──────────────────────────────────────────────
RULE 4 — propertyAddress CONSTRAINTS
──────────────────────────────────────────────

    ╔══════════════════════════════════════════════════════╗
    ║  CORE PRINCIPLE: The address value MUST NOT exceed   ║
    ║  10 words. Count every space-delimited token,        ║
    ║  including numbers, abbreviations, and ZIP codes.    ║
    ║  If the extracted address exceeds 10 words, truncate ║
    ║  by dropping the least critical components from the  ║
    ║  right (typically ZIP code first, then state, etc.), ║
    ║  preserving street number + name as the priority.    ║
    ╚══════════════════════════════════════════════════════╝

    a) WORD LIMIT — 10 WORDS MAXIMUM:
        - Count each space-delimited token as one word.
        - Numbers, abbreviations, ZIP codes, and directional prefixes (N, S, E, W, NE, SW)
          each count as one word.
        - If the address exceeds 10 words, truncate from the right, preserving:
            Priority 1 (must keep): Street number + street name
            Priority 2 (keep if space): Directional prefix/suffix (N, S, NW, etc.)
            Priority 3 (keep if space): Street type (St, Ave, Blvd, Dr, Rd, Ln, Way, etc.)
            Priority 4 (keep if space): City name
            Priority 5 (keep if space): State abbreviation
            Priority 6 (lowest):        ZIP code

        Truncation example (12 words → 10):
            "1024 N Rattlesnake Road Unit 4 Sierra Vista Arizona 85635 USA"
            Count: 1024(1) N(2) Rattlesnake(3) Road(4) Unit(5) 4(6) Sierra(7) Vista(8) Arizona(9) 85635(10) USA(11) → 11 words
            Drop from right: remove "USA"
            Result: "1024 N Rattlesnake Road Unit 4 Sierra Vista Arizona 85635"  (10 words ✓)

    b) VALID CONTENT: Must be a physical US situs/street address for real property located in
       or associated with Cochise County, AZ.

    c) REQUIRED COMPONENTS (include when present, subject to 10-word limit):
        - Street number
        - Street name and type (St, Ave, Blvd, Dr, Rd, etc.)
        - Unit/Suite/Apt number (if applicable)
        - City, State abbreviation, ZIP code

    d) STRICTLY EXCLUDED — never include these in propertyAddress:
        Legal descriptions       → Lot/Block/Tract/Section/Township/Range text
        Subdivision names        → "Mountain View Estates", "Vista Bella Unit 3"
        Parcel/APN numbers       → Any formatted parcel identifier
        Mailing/postal addresses → P.O. Box, "mail to:", "c/o"
        Recording boilerplate    → Instrument numbers, book/page references, Recorder's office stamps

    e) AMBIGUITY: If the document contains only a legal description and no street address,
       set to "NOT_FOUND". Do not fabricate or synthesize an address from a legal description.

    f) WORD COUNT ENFORCEMENT SUMMARY TABLE:

        Scenario                                      Action
        ──────────────────────────────────────────────────────────────────
        Valid address, ≤10 words                   → Use as-is
        Valid address, 11–15 words                 → Truncate from right per priority order above
        Valid address, >15 words (likely has legal → Strip legal description first, then apply
            description mixed in)                     10-word truncation to the remainder
        Only legal description present             → "NOT_FOUND"
        Only P.O. Box present                      → "NOT_FOUND"
        No address found                           → "NOT_FOUND"

    g) VALID EXAMPLES:
        "741 W CORONADO DRIVE SIERRA VISTA AZ 85635"      → 8 words ✓  VALID
        "88 S VISTA AVENUE BISBEE AZ 85603"               → 7 words ✓  VALID
        "1024 N RATTLESNAKE RD UNIT 4 SIERRA VISTA AZ 85635" → 10 words ✓ VALID (at limit)
        "123 N MAIN STREET DOUGLAS ARIZONA 85607 COCHISE COUNTY USA" → 10 words, truncate "COCHISE COUNTY USA" → "123 N MAIN STREET DOUGLAS ARIZONA 85607"
        "Lot 5 Block 3 Mountain View Estates Cochise Co"  → INVALID → "NOT_FOUND"
        "P.O. Box 1234 Bisbee AZ 85603"                  → INVALID → "NOT_FOUND"

════════════════════════════════════════════════
SECTION 4 — FEW-SHOT EXAMPLES
════════════════════════════════════════════════

The following three examples demonstrate correct end-to-end extraction. Study them carefully before processing the target document.

──────────────────────────────────────────────
EXAMPLE 1 — Standard Deed of Trust, Multiple Co-Borrowers, Long Address
──────────────────────────────────────────────

INPUT OCR TEXT:

RECORDING REQUESTED BY: FIRST AMERICAN TITLE COMPANY
WHEN RECORDED MAIL TO: QUICKEN LOANS INC, 1050 WOODWARD AVE, DETROIT MI 48226

DEED OF TRUST
Instrument No. 2023-004512   Recorded: 03/15/2023   Book 412 Page 88

THIS DEED OF TRUST is made on March 10, 2023. The trustor is
ROBERT C HENDERSON AND PATRICIA M HENDERSON, HUSBAND AND WIFE ("Borrower").
The trustee is FIDELITY NATIONAL TITLE AGENCY INC, an Arizona corporation.
The beneficiary is QUICKEN LOANS INC, its successors and assigns.

Loan amount: TWO HUNDRED FORTY-FIVE THOUSAND AND NO/100 DOLLARS ($245,000.00).

Property located at: 1024 NORTH RATTLESNAKE ROAD UNIT 4, SIERRA VISTA, ARIZONA 85635 USA.
Legal Description: Lot 14, Block 7, CORONADO HILLS SUBDIVISION UNIT 2, Book of Maps 22,
Page 15, Cochise County, AZ.  APN: 105-44-076.

EXTRACTION WALKTHROUGH:

  trustor:
    Raw:     "ROBERT C HENDERSON AND PATRICIA M HENDERSON, HUSBAND AND WIFE"
    Split:   ["ROBERT C HENDERSON", "PATRICIA M HENDERSON, HUSBAND AND WIFE"]
    Strip:   ["ROBERT C HENDERSON", "PATRICIA M HENDERSON"]  ← removed "HUSBAND AND WIFE"
    Words:   3 ✓, 3 ✓  (each ≤5)
    Join:    "ROBERT C HENDERSON, PATRICIA M HENDERSON"

  trustee:
    Raw:     "FIDELITY NATIONAL TITLE AGENCY INC, an Arizona corporation"
    Strip:   "FIDELITY NATIONAL TITLE AGENCY INC"  ← removed "an Arizona corporation"
    Words:   5 ✓  (exactly at limit)
    Final:   "FIDELITY NATIONAL TITLE AGENCY INC"

  beneficiary:
    Raw:     "QUICKEN LOANS INC, its successors and assigns"
    Strip:   "QUICKEN LOANS INC"  ← removed "its successors and assigns"
    Words:   3 ✓
    Final:   "QUICKEN LOANS INC"

  principalAmount:
    Raw:     "TWO HUNDRED FORTY-FIVE THOUSAND AND NO/100 DOLLARS ($245,000.00)"
    Convert: 245000.00
    Strip:   "245000.00"  ← no $, no commas
    Check:   ≥1000 ✓
    Final:   "245000.00"

  propertyAddress:
    Raw:     "1024 NORTH RATTLESNAKE ROAD UNIT 4, SIERRA VISTA, ARIZONA 85635 USA"
    Tokens:  1024(1) NORTH(2) RATTLESNAKE(3) ROAD(4) UNIT(5) 4(6) SIERRA(7) VISTA(8) ARIZONA(9) 85635(10) USA(11) = 11 words
    Exceeds 10 → drop lowest priority from right: remove "USA"
    Result:  "1024 NORTH RATTLESNAKE ROAD UNIT 4 SIERRA VISTA ARIZONA 85635"  (10 words ✓)

  grantors: ["ROBERT C HENDERSON", "PATRICIA M HENDERSON"]
  grantees: ["FIDELITY NATIONAL TITLE AGENCY INC"]
  confidenceNote: ""  ← all fields found

EXPECTED OUTPUT:
{
  "trustor": "ROBERT C HENDERSON, PATRICIA M HENDERSON",
  "trustee": "FIDELITY NATIONAL TITLE AGENCY INC",
  "beneficiary": "QUICKEN LOANS INC",
  "principalAmount": "245000.00",
  "propertyAddress": "1024 NORTH RATTLESNAKE ROAD UNIT 4 SIERRA VISTA ARIZONA 85635",
  "grantors": ["ROBERT C HENDERSON", "PATRICIA M HENDERSON"],
  "grantees": ["FIDELITY NATIONAL TITLE AGENCY INC"],
  "confidenceNote": ""
}

──────────────────────────────────────────────
EXAMPLE 2 — Quitclaim Deed, Three Co-Grantors, No Street Address
──────────────────────────────────────────────

INPUT OCR TEXT:

QUITCLAIM DEED
Recorded: 07/22/2022   Cochise County Recorder

MARGARET L VANCE AND THOMAS R VANCE AND CAROL ANN VANCE-WHITMORE, AS JOINT TENANTS,
do hereby quitclaim to VANCE FAMILY LIVING TRUST DATED JUNE 1 2019,
all right, title, and interest in the following property:

Lot 22, Block 4, APACHE MEADOWS SUBDIVISION, Book of Maps 18, Page 6, Cochise County, AZ.
APN: 212-67-022B.   No street address on record.   No monetary consideration.


EXTRACTION WALKTHROUGH:

  trustor / trustee / beneficiary:
    Document type: Quitclaim Deed — no trustor/trustee/beneficiary relationship exists.
    All three → "NOT_FOUND"

  principalAmount:
    "No monetary consideration" — no numeric value ≥ 1000 → "NOT_FOUND"

  propertyAddress:
    Only legal description present (Lot/Block/Subdivision). Rule 4d prohibits. Rule 4e applies.
    → "NOT_FOUND"

  grantors:
    Raw:   "MARGARET L VANCE AND THOMAS R VANCE AND CAROL ANN VANCE-WHITMORE, AS JOINT TENANTS"
    Split: ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE, AS JOINT TENANTS"]
    Strip: ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE"]  ← removed "AS JOINT TENANTS"
    Words: 3 ✓, 3 ✓, 3 ✓  (each ≤5, hyphenated counts as 1 token)
    Array: ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE"]

  grantees:
    Raw:   "VANCE FAMILY LIVING TRUST DATED JUNE 1 2019"
    Words: 8 — exceeds 5 → truncate to first 5: "VANCE FAMILY LIVING TRUST DATED"
    Array: ["VANCE FAMILY LIVING TRUST DATED"]

  confidenceNote: "NOT_FOUND:trustor,trustee,beneficiary,principalAmount,propertyAddress"

EXPECTED OUTPUT:
{
  "trustor": "NOT_FOUND",
  "trustee": "NOT_FOUND",
  "beneficiary": "NOT_FOUND",
  "principalAmount": "NOT_FOUND",
  "propertyAddress": "NOT_FOUND",
  "grantors": ["MARGARET L VANCE", "THOMAS R VANCE", "CAROL ANN VANCE-WHITMORE"],
  "grantees": ["VANCE FAMILY LIVING TRUST DATED"],
  "confidenceNote": "NOT_FOUND:trustor,trustee,beneficiary,principalAmount,propertyAddress"
}

──────────────────────────────────────────────
EXAMPLE 3 — OCR-Degraded Document, Long Beneficiary Name, Oversized Address
──────────────────────────────────────────────

INPUT OCR TEXT:

D££D 0F TRU$T — C0CHISE C0UNTY ARIZONA
Rec0rded: ??/??/2021   Instr#: 2021-00XXXX

Tru$tor: DAVID K MORALES AND LISA R MORALES-SANTIAGO, AN UNMARRIED COUPLE
Tru$tee: [ILLEGIBLE SMUDGE]
Benefici@ry: UNITED STATES NATIONAL BANK OF ARIZONA SOUTHWEST DIVISION, A CORPORATION,
             its successors and assigns

Loan Am0unt: $1O2,5OO.OO
Address: 88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE COCHISE COUNTY ARIZONA 85603 USA ATTN RECORDS
Legal Desc: SW 1/4 SEC 14 T22S R24E G&SRM Cochise Co AZ  APN 103-29-011


EXTRACTION WALKTHROUGH:

  trustor:
    Raw:   "DAVID K MORALES AND LISA R MORALES-SANTIAGO, AN UNMARRIED COUPLE"
    Split: ["DAVID K MORALES", "LISA R MORALES-SANTIAGO, AN UNMARRIED COUPLE"]
    Strip: ["DAVID K MORALES", "LISA R MORALES-SANTIAGO"]  ← removed "AN UNMARRIED COUPLE"
    Words: 3 ✓, 3 ✓
    Join:  "DAVID K MORALES, LISA R MORALES-SANTIAGO"

  trustee:
    Raw:   "[ILLEGIBLE SMUDGE]" — not a real entity name → "NOT_FOUND"

  beneficiary:
    Raw:   "UNITED STATES NATIONAL BANK OF ARIZONA SOUTHWEST DIVISION, A CORPORATION, its successors and assigns"
    Strip: "UNITED STATES NATIONAL BANK OF ARIZONA SOUTHWEST DIVISION"  ← removed "A CORPORATION" and "its successors and assigns"
    Words: 9 — exceeds 5 → truncate to first 5: "UNITED STATES NATIONAL BANK OF"
    Final: "UNITED STATES NATIONAL BANK OF"

  principalAmount:
    Raw:   "$1O2,5OO.OO" — OCR substituted letter O for digit 0
    Fix:   "$102,500.00"
    Strip: "102500.00"
    Check: ≥1000 ✓
    Final: "102500.00"

  propertyAddress:
    Raw:    "88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE COCHISE COUNTY ARIZONA 85603 USA ATTN RECORDS"
    Tokens: 88(1) SOUTH(2) VISTA(3) AVENUE(4) APARTMENT(5) 3B(6) BISBEE(7) COCHISE(8) COUNTY(9) ARIZONA(10) 85603(11) USA(12) ATTN(13) RECORDS(14) = 14 words
    Exceeds 10 → drop from right per priority:
        Remove RECORDS(14), ATTN(13), USA(12), COUNTY(9+COCHISE(8 — these are non-address filler
        Careful truncation to 10: "88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE ARIZONA 85603"
        Count: 88(1) SOUTH(2) VISTA(3) AVENUE(4) APARTMENT(5) 3B(6) BISBEE(7) ARIZONA(8) 85603(9) = 9 words ✓

  grantors: ["DAVID K MORALES", "LISA R MORALES-SANTIAGO"]
  grantees: ["UNITED STATES NATIONAL BANK OF"]
  confidenceNote: "NOT_FOUND:trustee"

EXPECTED OUTPUT:
{
  "trustor": "DAVID K MORALES, LISA R MORALES-SANTIAGO",
  "trustee": "NOT_FOUND",
  "beneficiary": "UNITED STATES NATIONAL BANK OF",
  "principalAmount": "102500.00",
  "propertyAddress": "88 SOUTH VISTA AVENUE APARTMENT 3B BISBEE ARIZONA 85603",
  "grantors": ["DAVID K MORALES", "LISA R MORALES-SANTIAGO"],
  "grantees": ["UNITED STATES NATIONAL BANK OF"],
  "confidenceNote": "NOT_FOUND:trustee"
}

════════════════════════════════════════════════
SECTION 5 — ABSOLUTE OUTPUT REQUIREMENTS
════════════════════════════════════════════════

1. Your response MUST be ONLY the JSON object — no preamble, no explanation, no markdown fences (no ```json), no trailing commentary.
2. The JSON must be syntactically valid and parseable by a standard JSON parser.
3. All 8 keys must be present in every response, even if values are "NOT_FOUND" or [].
4. Do not invent, hallucinate, or infer data not present in the source text.
5. Do not merge or confuse fields — a name found in a "mail to" header is NOT a beneficiary.
6. If the document is entirely unreadable or clearly not a real estate instrument, return:
   {
     "trustor": "NOT_FOUND",
     "trustee": "NOT_FOUND",
     "beneficiary": "NOT_FOUND",
     "principalAmount": "NOT_FOUND",
     "propertyAddress": "NOT_FOUND",
     "grantors": [],
     "grantees": [],
     "confidenceNote": "NOT_FOUND:trustor,trustee,beneficiary,principalAmount,propertyAddress"
   }
""".strip()


_NUMERIC_AMOUNT_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")


def _strict_valuation_disabled() -> bool:
    env_key = _county_env_key()
    candidates = [
        "DISABLE_STRICT_VALUATION",
        f"{env_key}_DISABLE_STRICT_VALUATION",
        f"{env_key.replace('_', '')}_DISABLE_STRICT_VALUATION",
    ]
    for k in candidates:
        if str(os.getenv(k, "")).strip() == "1":
            return True
    return False


def _normalize_principal_amount_numeric(value: str) -> str:
    """Return numeric-only principal amount string or empty string if invalid."""
    s = str(value or "").strip()
    if not s or s.upper() == "NOT_FOUND" or s == "NOT_FOUND":
        return ""
    s = re.sub(r"[,$\s]", "", s)
    if not s:
        return ""

    if not _NUMERIC_AMOUNT_RE.match(s):
        # Lenient mode: keep non-numeric raw values (e.g., 'No monetary consideration')
        # instead of forcing NOT_FOUND.
        return "" if not _strict_valuation_disabled() else str(value or "").strip()

    try:
        val = float(s)
    except Exception:
        return "" if not _strict_valuation_disabled() else str(value or "").strip()

    # Strict mode previously dropped values < 1000.00; lenient mode keeps them.
    if (not _strict_valuation_disabled()) and val < 1000:
        return ""

    return f"{val:.2f}".rstrip("0").rstrip(".")


def _normalise_date(date_str: str) -> str:
    s = (date_str or "").strip()
    for fmt in ("%m/%d/%Y", "%-m/%-d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%-m/%-d/%Y")
        except Exception:
            pass
    return s


def _cookie_header_from_cookies(cookies: list[dict]) -> str:
    vals = []
    for c in cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        if name:
            vals.append(f"{name}={value}")
    return "; ".join(vals)


def _load_local_env() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ[k] = v
    except Exception:
        return


def _make_session(cookie_header: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        s.cookies.set(k.strip(), v.strip(), domain="www.thecountyrecorder.com")
    return s


def _safe_text(node: Any) -> str:
    if not node:
        return ""
    if hasattr(node, "get_text"):
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return re.sub(r"\s+", " ", str(node)).strip()


def _extract_date(text: str) -> str:
    m = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
    return m.group(0) if m else ""


def _extract_recording_number(text: str) -> str:
    for pat in [r"\b\d{4}-\d{5,}\b", r"\b\d{7,}\b"]:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return ""


def _extract_value_by_label(soup: BeautifulSoup, labels: list[str]) -> str:
    for label in labels:
        tag = soup.find(string=re.compile(rf"\b{re.escape(label)}\b", re.I))
        if not tag:
            continue
        parent = tag.parent
        if parent:
            next_td = parent.find_next("td")
            if next_td and next_td is not parent:
                v = _safe_text(next_td)
                if v and label.lower() not in v.lower():
                    return v
        nxt = tag.find_next(string=True)
        if nxt:
            v = _safe_text(nxt)
            if v and label.lower() not in v.lower():
                return v
    return ""


def _value_by_id_contains(soup: BeautifulSoup, key: str) -> str:
    node = soup.select_one(f"input[id*='{key}'], textarea[id*='{key}']")
    if not node:
        return ""
    if node.name == "textarea":
        return _safe_text(node)
    return (node.get("value") or "").strip()


def _collect_detail_text(soup: BeautifulSoup) -> str:
    """Collect document-focused text blocks and avoid full-page nav noise."""
    blocks: list[str] = []
    selectors = [
        "table[id*='Table7']",
        "table[id*='DescriptionTable']",
        "table[id*='tableNameIndexingDetails']",
        "table[id*='tableRelatedDocumentDetails']",
        "span[id*='lblViewImage']",
    ]
    for sel in selectors:
        for node in soup.select(sel):
            txt = "\n".join(s.strip() for s in node.stripped_strings if s and s.strip())
            if txt:
                blocks.append(txt)

    if not blocks:
        for node in soup.select("table.Results"):
            txt = "\n".join(s.strip() for s in node.stripped_strings if s and s.strip())
            if txt:
                blocks.append(txt)

    if not blocks:
        return _safe_text(soup)

    uniq = list(dict.fromkeys(blocks))
    return "\n\n".join(uniq)


def _extract_named_rows_by_label(soup: BeautifulSoup, label: str) -> list[str]:
    out: list[str] = []
    target = (label or "").strip().upper()
    for tbl in soup.select("table.Results"):
        vals = [re.sub(r"\s+", " ", s).strip(" ,") for s in tbl.stripped_strings if s and s.strip()]
        if not vals:
            continue
        head = (vals[0] or "").upper()
        if head != target:
            continue
        for v in vals[1:]:
            if not v:
                continue
            if v.upper() in {"SHOW NAME INDEXING DETAILS", "HIDE NAME INDEXING DETAILS"}:
                continue
            out.append(v)
    return out


def _extract_image_like_urls(raw_html: str) -> list[str]:
    """Extract direct/indirect image URLs from detail page HTML/JS blobs."""
    if not raw_html:
        return []
    out: list[str] = []
    seen = set()

    patterns = [
        r"((?:ImageHandler|ViewImage)\.aspx?[^\"'\s)<>]+)",
        r"(ImageHandler\.ashx\?[^\"'\s)<>]+)",
    ]

    for pat in patterns:
        for m in re.finditer(pat, raw_html, re.I):
            cand = (m.group(1) or "").strip()
            if not cand:
                continue
            cand = cand.replace("&amp;", "&")
            full = urllib.parse.urljoin(BASE_URL + "/", cand)
            if full not in seen:
                seen.add(full)
                out.append(full)

    return out


def _select_option_containing(page: Any, sel: str, target: str, timeout: int = 5000) -> bool:
    """Select first option in <select> whose text contains target (case-insensitive)."""
    def _norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())

    try:
        locator = page.locator(sel).first
        opts = locator.evaluate(
            "el => Array.from(el.options).map(o => ({text: o.text.trim(), value: o.value}))"
        )
        tgt_upper = (target or "").upper()
        tgt_norm = _norm(target)
        tgt_tokens = [t for t in re.split(r"\W+", tgt_upper) if t]

        # Pass 0: exact match (raw or normalized) to prevent accidental partial hits.
        for opt in opts:
            txt = (opt.get("text") or "").strip()
            if not txt or txt.upper() in {"LOADING..."}:
                continue
            txt_up = txt.upper()
            txt_norm = _norm(txt)
            if (tgt_upper and txt_up == tgt_upper) or (tgt_norm and txt_norm == tgt_norm):
                locator.select_option(value=opt["value"], timeout=timeout)
                return True

        # Pass 1: direct contains
        for opt in opts:
            txt = (opt.get("text") or "")
            if tgt_upper and tgt_upper in txt.upper():
                locator.select_option(value=opt["value"], timeout=timeout)
                return True

        # Pass 2: normalized contains (handles apostrophes, extra spaces, punctuation)
        for opt in opts:
            txt = (opt.get("text") or "").strip()
            txt_norm = _norm(txt)
            if not txt_norm or txt.upper() in {"LOADING..."}:
                continue
            if tgt_norm and tgt_norm in txt_norm:
                locator.select_option(value=opt["value"], timeout=timeout)
                return True

        # Pass 3: token-based fuzzy match
        for opt in opts:
            txt_up = (opt.get("text") or "").upper()
            score = sum(1 for t in tgt_tokens if t and t in txt_up)
            if score >= max(2, len(tgt_tokens) - 1):
                locator.select_option(value=opt["value"], timeout=timeout)
                return True
    except Exception:
        pass
    return False


def _doc_type_candidates(doc_type: str) -> list[str]:
    """Return preferred aliases for counties that label document types differently."""
    dt = (doc_type or "").upper().strip()
    aliases: dict[str, list[str]] = {
        "NOTICE OF DEFAULT": [
            "NOTICE OF DEFAULT",
            "NOTICE OF ELECTION",
            "NOTICE OF BREACH",
            "NOD",
        ],
        "NOTICE OF TRUSTEE SALE": ["NOTICE OF TRUSTEE SALE", "TRUSTEE SALE"],
        "NOTICE OF REINSTATEMENT": ["NOTICE OF REINSTATEMENT", "REINSTATEMENT"],
        "LIS PENDENS": ["LIS PENDENS"],
        "DEED IN LIEU": ["DEED IN LIEU"],
        "TREASURERS DEED": ["TREASURERS DEED", "TREASURER'S DEED"],
    }
    return aliases.get(dt, [doc_type])


def parse_results_html(html: str, source_doc_type: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict] = []
    seen = set()
    for a in soup.select("a[href*='Document.aspx?DK=']"):
        href = a.get("href", "")
        m = re.search(r"DK=(\d+)", href)
        if not m:
            continue
        dk = m.group(1)
        if dk in seen:
            continue
        seen.add(dk)
        row = a.find_parent("tr") or a.find_parent("div") or a.parent
        row_text = _safe_text(row)
        rec = {
            "documentId": dk,
            "recordingNumber": _extract_recording_number(row_text),
            "recordingDate": _extract_date(row_text),
            "documentType": source_doc_type or "",
            "grantors": "",
            "grantees": "",
            "trustor": "",
            "trustee": "",
            "beneficiary": "",
            "principalAmount": "",
            "propertyAddress": "",
            "detailUrl": f"{DOCUMENT_URL}?DK={dk}",
            "imageUrls": "",
            "ocrMethod": "",
            "ocrChars": 0,
            "sourceCounty": COUNTY_DISPLAY,
            "analysisError": "",
        }
        if not rec["recordingDate"]:
            tds = row.find_all("td") if row else []
            for td in tds:
                d = _extract_date(_safe_text(td))
                if d:
                    rec["recordingDate"] = d
                    break
        if row:
            tds = row.find_all("td")
            for td in tds:
                tx = _safe_text(td).upper()
                if any(x in tx for x in ("DEED", "LIEN", "TRUSTEE", "PENDENS", "SALE")):
                    rec["documentType"] = tx
                    break
        records.append(rec)
    return records


def _goto_document_search(page: Any, verbose: bool = False) -> None:
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)
    
    # Step 1: Click Continue on Search.aspx
    for sel in ["#MainContent_Button1", "input[id*='Button1'][value*='Continue']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=8000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1200)
                break
            except Exception:
                pass
    
    # Step 2: On Default.aspx, select State
    state_sel = "select[id*='cboStates'], select[name*='cboStates']"
    if page.locator(state_sel).count() > 0:
        try:
            page.locator(state_sel).first.select_option(label="ARIZONA")
            if verbose:
                print(f"  Select 'ARIZONA': ok")
        except Exception:
            if verbose:
                print(f"  Select 'ARIZONA': failed")
        page.wait_for_timeout(800)
    
    # Step 3: Select County (auto-navigates to Disclaimer.aspx)
    county_sel = "select[id*='cboCounties'], select[name*='cboCounties']"
    if page.locator(county_sel).count() > 0:
        try:
            page.locator(county_sel).first.select_option(label=COUNTY_LABEL)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:
                pass
            if verbose:
                print(f"  Select '{COUNTY_LABEL}': ok")
        except Exception:
            if verbose:
                print(f"  Select '{COUNTY_LABEL}': failed")
        page.wait_for_timeout(2000)
    
    # Step 4: Accept Disclaimer (on Disclaimer.aspx)
    for sel in ["input[id*='btnAccept']", "#MainContent_searchMainContent_ctl01_btnAccept"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=8000)
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1500)
                break
            except Exception:
                pass
    
    # Step 5: Navigate to search form (from Introduction.aspx)
    if "Search.aspx" not in (page.url or ""):
        for sel in ["a:has-text('Search Document')", "a#TreeView1t6", "a[href*='Search.aspx']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    page.wait_for_timeout(1200)
                    break
                except Exception:
                    pass
    
    # Step 6: Final fallback to ensure we're on Search.aspx
    if "Search.aspx" not in (page.url or ""):
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)


def _execute_search_for_doc_type(
    page: Any,
    start_date: str,
    end_date: str,
    doc_type: str,
    verbose: bool = False,
) -> bool:
    sd = _normalise_date(start_date)
    ed = _normalise_date(end_date)
    date_ok = False
    for ssel, esel in [
        ("input[id*='tbDateStart']", "input[id*='tbDateEnd']"),
    ]:
        if page.locator(ssel).count() > 0 and page.locator(esel).count() > 0:
            page.locator(ssel).first.fill(sd)
            page.locator(esel).first.fill(ed)
            date_ok = True
            break
    type_ok = False
    group_sel = "select[id*='cboDocumentGroup']"
    type_sel = "select[id*='cboDocumentType']"
    load_types_btn = "input[id*='btnLoadDocumentTypes']"

    candidates = _doc_type_candidates(doc_type)

    # Fast path: on repeated searches, the document type list is often already loaded.
    if page.locator(type_sel).count() > 0:
        try:
            for cand in candidates:
                if _select_option_containing(page, type_sel, cand):
                    type_ok = True
                    break
        except Exception:
            type_ok = False

    if page.locator(group_sel).count() > 0:
        group_map = {
            "Notice": [
                "NOTICE OF DEFAULT",
                "NOTICE OF TRUSTEE SALE",
                "NOTICE OF REINSTATEMENT",
            ],
            "Court": ["LIS PENDENS"],
            "Deed": ["DEED IN LIEU", "TREASURERS DEED", "TRUSTEES DEED", "SHERIFFS DEED"],
            "Lien": ["STATE LIEN", "STATE TAX LIEN"],
        }
        group_order = ["Notice", "Court", "Deed", "Lien", "Release", "Other"]
        selected_group = ""
        for group, types in group_map.items():
            if any(t.lower() in doc_type.lower() or doc_type.lower() in t.lower() for t in types):
                selected_group = group
                break

        # If not mapped, still try common groups.
        groups_to_try = [selected_group] if selected_group else []
        groups_to_try.extend([g for g in group_order if g and g != selected_group])

        for group in groups_to_try:
            if type_ok:
                break
            if not group:
                continue
            if _select_option_containing(page, group_sel, group):
                page.wait_for_timeout(500)
                # Some sessions require explicit "Load" click to populate Document Type options.
                if page.locator(load_types_btn).count() > 0:
                    try:
                        page.locator(load_types_btn).first.click(timeout=8000)
                        page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    except Exception:
                        pass
                # Also trigger onfocus loader used by the site JS, then wait for options.
                try:
                    if page.locator(type_sel).count() > 0:
                        page.locator(type_sel).first.focus()
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                if page.locator(type_sel).count() > 0:
                    # Wait until placeholder "Loading..." is replaced (max ~5s)
                    for _ in range(10):
                        try:
                            opts = page.locator(type_sel).first.evaluate(
                                "el => Array.from(el.options).map(o => (o.text || '').trim())"
                            )
                            has_real = any(o and o.upper() != "LOADING..." for o in opts)
                            if has_real:
                                break
                        except Exception:
                            pass
                        page.wait_for_timeout(500)
                    for cand in candidates:
                        if _select_option_containing(page, type_sel, cand):
                            type_ok = True
                            break
                    # Validate that selected option is really the requested type.
                    if type_ok:
                        try:
                            selected_text = page.locator(type_sel).first.evaluate(
                                "el => (el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '').trim()"
                            )
                            wanted_list = [re.sub(r"[^A-Z0-9]+", "", c.upper()) for c in candidates]
                            got = re.sub(r"[^A-Z0-9]+", "", (selected_text or "").upper())
                            if not got or not any(w and w in got for w in wanted_list):
                                type_ok = False
                        except Exception:
                            type_ok = False
                if type_ok:
                    break
        page.wait_for_timeout(400)
    if verbose:
        type_status = "ok" if type_ok else "unavailable"
        print(f"  Search setup: dates={'ok' if date_ok else 'fail'} type={type_status}")
        if not type_ok and page.locator(type_sel).count() > 0:
            try:
                opts = page.locator(type_sel).first.evaluate(
                    "el => Array.from(el.options).map(o => (o.text || '').trim()).filter(Boolean)"
                )
                sample = [o for o in opts if "NOTICE" in o.upper()][:8]
                if sample:
                    print(f"  Available NOTICE types: {', '.join(sample)}")
            except Exception:
                pass
    if not type_ok:
        return False
    for sel in ["input[id*='btnSearchDocuments']", "input[type='submit'][value*='Execute Search']"]:
        if page.locator(sel).count() > 0:
            try:
                page.locator(sel).first.click(timeout=10_000)
                page.wait_for_load_state("domcontentloaded", timeout=45_000)
                page.wait_for_timeout(1200)
                return True
            except Exception:
                pass
    try:
        page.evaluate("() => { const form = document.querySelector('form'); if (form) form.submit(); }")
        page.wait_for_load_state("domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        return True
    except Exception:
        return False


def _collect_result_pages(page: Any, max_pages: int = 0, verbose: bool = False) -> list[str]:
    pages: list[str] = []
    page_no = 1
    visited_fingerprints: set[str] = set()
    while True:
        html = page.content()
        fingerprint = re.sub(r"\s+", "", page.url + (html[:1000] if html else ""))[:1200]
        if fingerprint in visited_fingerprints:
            break
        visited_fingerprints.add(fingerprint)
        pages.append(html)
        if verbose:
            print(f"    Collected results page {page_no}")
        if max_pages and page_no >= max_pages:
            break
        moved = False
        for sel in ["a:has-text('Next')", "a[title*='Next']"]:
            if page.locator(sel).count() > 0:
                try:
                    page.locator(sel).first.click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    page.wait_for_timeout(1000)
                    moved = True
                    break
                except Exception:
                    pass
        if not moved:
            break
        page_no += 1
    return pages


def playwright_collect_results(
    start_date: str,
    end_date: str,
    doc_types: list[str],
    max_pages: int = 0,
    headless: bool = True,
    verbose: bool = False,
) -> tuple[str, list[dict]]:
    if not _PLAYWRIGHT_OK:
        raise RuntimeError("playwright not installed")
    all_records: list[dict] = []
    seen = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        # Start fresh each run. Reusing stale storage state often causes
        # "County Selection Missing" and breaks document type loading.
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        _goto_document_search(page, verbose=verbose)
        for dt in doc_types:
            if verbose:
                print(f"[{COUNTY_DISPLAY.upper()}] Searching doc type: {dt}")
            ok = _execute_search_for_doc_type(page, start_date, end_date, dt, verbose=verbose)
            if not ok:
                # Retry once after reloading search form (handles first-search timing glitches).
                _goto_document_search(page, verbose=False)
                ok = _execute_search_for_doc_type(page, start_date, end_date, dt, verbose=verbose)
            if not ok:
                _goto_document_search(page, verbose=False)
                continue
            html_pages = _collect_result_pages(page, max_pages=max_pages, verbose=verbose)
            for html in html_pages:
                recs = parse_results_html(html, source_doc_type=dt)
                for r in recs:
                    dk = r.get("documentId", "")
                    if dk and dk not in seen:
                        seen.add(dk)
                        all_records.append(r)
            _goto_document_search(page, verbose=False)
        context.storage_state(path=str(STORAGE_STATE_PATH))
        cookie_header = _cookie_header_from_cookies(context.cookies())
        browser.close()
    return cookie_header, all_records


def fetch_detail(dk: str, session: requests.Session, timeout: int = 30) -> dict:
    url = f"{DOCUMENT_URL}?DK={dk}"
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = _collect_detail_text(soup)
    detail = {
        "detailUrl": url,
        "recordingNumber": _value_by_id_contains(soup, "tbReceptionNo") or "",
        "recordingDate": _value_by_id_contains(soup, "tbReceptionDate") or "",
        "documentType": _value_by_id_contains(soup, "tbDocumentType") or "",
        "grantors": "",
        "grantees": "",
        "trustor": "",
        "trustee": "",
        "beneficiary": "",
        "principalAmount": "",
        "propertyAddress": "",
        "rawText": text,
        "imageUrls": [],
        "imageAccessNote": "",
    }
    grantor_names = _extract_named_rows_by_label(soup, "Grantor")
    grantee_names = _extract_named_rows_by_label(soup, "Grantee")

    if grantor_names:
        detail["grantors"] = " | ".join(grantor_names[:4])
    if grantee_names:
        detail["grantees"] = " | ".join(grantee_names[:4])

    # Fallback only when labeled blocks are missing.
    if not detail.get("grantors") and not detail.get("grantees"):
        name_rows = soup.select("table[id*='tableNameIndexingDetails'] tr")
        parsed_names: list[str] = []
        for tr in name_rows[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue
            parts = [_safe_text(td) for td in tds[:4]]
            full = " ".join([p for p in parts if p]).strip()
            if full:
                parsed_names.append(full)
        if parsed_names and not detail.get("grantors"):
            detail["grantors"] = " | ".join(parsed_names[:1])
        if len(parsed_names) > 1 and not detail.get("grantees"):
            detail["grantees"] = " | ".join(parsed_names[1:2])
    desc = _value_by_id_contains(soup, "tbDescription")
    if desc:
        detail["rawText"] = (detail["rawText"] + "\n" + desc).strip()
    image_note = _safe_text(soup.select_one("span[id*='lblViewImage']"))
    if image_note:
        detail["imageAccessNote"] = image_note
    if not detail["recordingDate"]:
        detail["recordingDate"] = _extract_date(text)
    if not detail["recordingNumber"]:
        detail["recordingNumber"] = _extract_recording_number(text)

    # Extract property/location fields from tabular detail sections.
    # These fields are critical for counties that block unofficial images.
    street = ""
    city = ""
    parcel_id = ""
    lot = ""
    block = ""
    subdivision = ""

    # Preferred stable IDs used by county-recorder detail layouts.
    street_cells = soup.select("table[id$='Table6'] tr.Input td")
    if len(street_cells) >= 1:
        street = _safe_text(street_cells[0])
    if len(street_cells) >= 2:
        city = _safe_text(street_cells[1])

    parcel_cells = soup.select("table[id$='Table4'] tr.Input td")
    if parcel_cells:
        parcel_id = _safe_text(parcel_cells[0])

    legal_cells = soup.select("table[id$='Table1'] tr.Input td")
    if len(legal_cells) >= 1:
        lot = _safe_text(legal_cells[0])
    if len(legal_cells) >= 2:
        block = _safe_text(legal_cells[1])
    if len(legal_cells) >= 3:
        subdivision = _safe_text(legal_cells[2])

    # Fallback table scans by header text when IDs vary by county/account.
    if not parcel_id:
        for tbl in soup.select("table"):
            vals = [s.strip() for s in tbl.stripped_strings if s and s.strip()]
            if not vals:
                continue
            if vals[0].upper() == "PARCEL ID" and len(vals) > 1:
                parcel_id = vals[1]
                break

    if not street and not city:
        for tbl in soup.select("table"):
            vals = [s.strip() for s in tbl.stripped_strings if s and s.strip()]
            if len(vals) < 3:
                continue
            if vals[0].upper() == "STREET" and vals[1].upper() == "CITY":
                # next values are first data row, if available
                if len(vals) >= 4:
                    street = vals[2]
                if len(vals) >= 5:
                    city = vals[3]
                break

    address_from_detail = ""
    if street and city:
        address_from_detail = f"{street}, {city}"
    elif street:
        address_from_detail = street
    elif parcel_id:
        address_from_detail = f"Parcel ID {parcel_id}"

    if address_from_detail and not detail.get("propertyAddress"):
        detail["propertyAddress"] = address_from_detail

    # Make important non-image fields available to regex/LLM fallback by
    # appending compact normalized lines into rawText.
    enriched_bits: list[str] = []
    if street:
        enriched_bits.append(f"Street: {street}")
    if city:
        enriched_bits.append(f"City: {city}")
    if parcel_id:
        enriched_bits.append(f"Parcel ID: {parcel_id}")
    legal_parts = [
        f"Lot {lot}" if lot else "",
        f"Block {block}" if block else "",
        subdivision if subdivision else "",
    ]
    legal_line = " ".join(x for x in legal_parts if x).strip()
    if legal_line:
        enriched_bits.append(f"Legal: {legal_line}")
    if enriched_bits:
        detail["rawText"] = (detail["rawText"] + "\n" + "\n".join(enriched_bits)).strip()

    found = set()
    for tag in soup.select("a[href*='ImageHandler.ashx']"):
        u = tag.get("href") or ""
        if u:
            full = urllib.parse.urljoin(BASE_URL + "/", u)
            if full not in found:
                found.add(full)
                detail["imageUrls"].append(full)

    # Many records expose image links in JS/onclick instead of plain anchors.
    for node in soup.select("*[onclick], a[href], img[src], iframe[src]"):
        for attr in ["onclick", "href", "src"]:
            raw = (node.get(attr) or "").strip()
            if not raw:
                continue
            for full in _extract_image_like_urls(raw):
                if full not in found:
                    found.add(full)
                    detail["imageUrls"].append(full)

    # Last-pass regex scan over full HTML for embedded URL strings.
    for full in _extract_image_like_urls(r.text):
        if full not in found:
            found.add(full)
            detail["imageUrls"].append(full)
    return detail


def discover_image_urls(
    dk: str,
    session: requests.Session,
    detail_image_urls: list[str] | None = None,
    max_probe_pages: int = 6,
) -> list[str]:
    urls: list[str] = []
    seen = set()
    for u in (detail_image_urls or []):
        if u not in seen:
            seen.add(u)
            urls.append(u)

    # Resolve image viewer pages that contain actual ImageHandler URLs.
    viewer_candidates = [
        f"{BASE_URL}/ViewImage.aspx?DK={dk}",
        f"{BASE_URL}/ViewImage.aspx?dk={dk}",
    ]
    for vu in viewer_candidates:
        try:
            rr = session.get(vu, timeout=15, allow_redirects=True)
            if rr.status_code != 200:
                continue
            for full in _extract_image_like_urls(rr.text):
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
        except Exception:
            continue
    misses = 0
    for pn in range(1, max_probe_pages + 1):
        u = f"{IMAGE_HANDLER_URL}?DK={dk}&PN={pn}"
        try:
            head = session.head(u, timeout=10)
            ctype = (head.headers.get("Content-Type") or "").lower()
            cl = int(head.headers.get("Content-Length") or 0)
            if head.status_code == 200 and "image" in ctype and cl > 500:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
                misses = 0
                continue
        except Exception:
            pass

        # Many county-recorder sessions do not return useful HEAD metadata;
        # fall back to GET validation before declaring the page missing.
        try:
            rr = session.get(u, timeout=15, allow_redirects=True)
            ctype = (rr.headers.get("Content-Type") or "").lower()
            if rr.status_code == 200 and "image" in ctype and len(rr.content) > 500:
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
                misses = 0
                continue
        except Exception:
            pass

        misses += 1
        if misses >= 2 and pn > 1:
            break
    return urls


def _preprocess_for_ocr(im: Image.Image) -> Image.Image:
    """Upscale 2x + sharpen + increase contrast for OCR."""
    im = im.convert("RGB")
    w, h = im.size
    if w < 1200:
        im = im.resize((w * 2, h * 2), Image.LANCZOS)
    im = im.filter(ImageFilter.SHARPEN)
    im = ImageEnhance.Contrast(im).enhance(1.8)
    return im


def _ocr_from_image_bytes(data: bytes) -> str:
    if not data or pytesseract is None:
        return ""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im_proc = _preprocess_for_ocr(im)
        buf = io.BytesIO()
        im_proc.save(buf, format="PNG")
        buf.seek(0)
        im_clean = Image.open(buf)
        im_clean.load()
        return pytesseract.image_to_string(im_clean, config="--psm 6 --oem 3") or ""
    except Exception:
        return ""


def ocr_document_images(
    image_urls: list[str],
    session: requests.Session,
    timeout: int = 30,
    max_pages: int = 6,
) -> tuple[str, str]:
    texts: list[str] = []
    used = "none"
    for i, u in enumerate(image_urls[:max_pages], 1):
        try:
            rr = session.get(u, timeout=timeout)
            rr.raise_for_status()
            ctype = (rr.headers.get("Content-Type") or "").lower()
            if "image" not in ctype:
                continue
            txt = _ocr_from_image_bytes(rr.content)
            if txt.strip():
                used = "tesseract-image"
                texts.append(f"\n\n--- PAGE {i} ---\n{txt.strip()}")
        except Exception:
            continue
    return "\n".join(texts).strip(), used


def _regex_principal(text: str) -> str:
    def _format_money(raw: str) -> str:
        return _normalize_principal_amount_numeric(raw)

    pats = [
        r"(?:original\s+principal(?:\s+amount)?|principal\s+balance|unpaid\s+principal(?:\s+balance)?|loan\s+amount|amount\s+of\s+the\s+indebtedness|sum\s+of)[^\d\n]{0,80}(\$?\s*\d[\d,]*(?:\.\d{2})?)",
        r"(?:principal|indebtedness)[^\d\n]{0,40}(\$?\s*\d[\d,]*(?:\.\d{2})?)",
    ]
    for p in pats:
        m = re.search(p, text, re.I | re.S | re.M)
        if m:
            val = _format_money(m.group(1).strip())
            if val:
                return val

    for ln in (text or "").splitlines():
        line = ln.strip()
        if not line:
            continue
        u = line.upper()
        if not any(k in u for k in ["PRINCIPAL", "INDEBTEDNESS", "LOAN AMOUNT", "UNPAID BALANCE"]):
            continue
        amounts = re.findall(r"\$?\s*\d[\d,]*(?:\.\d{2})?", line)
        if not amounts:
            continue
        # Prefer the largest amount on the principal-indicative line.
        best = ""
        best_val = 0.0
        for amt in amounts:
            fm = _format_money(amt)
            if not fm:
                continue
            v = float(re.sub(r"[^\d.]", "", fm))
            if v > best_val:
                best_val = v
                best = fm
        if best:
            return best
    return ""


_ADDRESS_NOISE_PATTERNS = [
    r"\bREQUESTED\s+BY\b",
    r"\bWHEN\s+RECORDED\s+MAIL\s+TO\b",
    r"\bRETURN\s+TO\b",
    r"\bATTN\b",
    r"\bNAME\s+AND\s+ADDRESS\b",
    r"\bRECORDING\s+FEE\b",
    r"\bTHIS\s+SECURITY\s+INSTRUMENT\b",
    r"\bTHE\s+ABOVE\s+DESCRIBED\b",
    r"\bSITUATED\s+IN\s+THE\s+COUNTY\b",
    r"\bCOUNTY\s+SELECTION\s+MISSING\b",
    r"\bSKIP\s+NAVIGATION\s+LINKS\b",
    r"\b0\s+ITEMS\s+IN\s+CART\b",
    r"\bPO\s*BOX\b",
]

_ADDRESS_STREET_SUFFIX_RE = re.compile(
    r"\b(ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|WAY|HWY|HIGHWAY|PKWY|PARKWAY|CIR|CIRCLE|TRL|TRAIL)\b",
    re.I,
)


def _extract_relevant_address_fragment(value: str) -> str:
    """Extract the most address-like fragment from noisy OCR/LLM text."""
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,;:-")
    if not text:
        return ""

    m_parcel = re.search(r"\bPARCEL\s+ID\s*[:#-]?\s*([A-Z0-9\-]{3,})\b", text, re.I)
    if m_parcel:
        return f"Parcel ID {m_parcel.group(1).strip()}"

    # Prefer explicit street address fragments (with optional city/state/zip tail).
    m_street = re.search(
        r"\b\d{1,6}\s+(?:[NSEW]\.?(?:\s+|$))?[A-Z0-9][A-Za-z0-9\s.#\-']{2,95}?\b"
        r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|WAY|HWY|HIGHWAY|PKWY|PARKWAY|CIR|CIRCLE|TRL|TRAIL)\b"
        r"(?:,\s*[A-Z][A-Za-z .'-]+(?:,\s*[A-Z]{2}(?:\s+\d{5}(?:-\d{4})?)?)?)?",
        text,
        re.I,
    )
    if m_street:
        return m_street.group(0).strip(" ,;:-")

    # Fallback for legal-only location strings.
    m_legal = re.search(
        r"\b(?:LOT\s+\w+[\w\-]*)(?:\s+BLOCK\s+\w+[\w\-]*)?(?:\s+SUBDIVISION\s+[A-Za-z0-9\s\-']+)?\b",
        text,
        re.I,
    )
    if m_legal:
        return m_legal.group(0).strip(" ,;:-")

    return text


def sanitize_property_address(value: str) -> str:
    v = _extract_relevant_address_fragment(value)
    if not v:
        return ""

    # Remove common leading labels but keep the actual value.
    v = re.sub(
        r"^(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as|property\s+located\s+at|located\s+at)\s*[:\-]\s*",
        "",
        v,
        flags=re.I,
    ).strip(" ,;:-")

    # Truncate at known non-address sections when OCR joins multiple clauses.
    v = re.split(
        r"\b(?:APN|ASSESSOR(?:'S)?\s+PARCEL|REQUESTED\s+BY|WHEN\s+RECORDED|TOGETHER\s+WITH|THIS\s+SECURITY\s+INSTRUMENT|LEGAL\s+DESCRIPTION|RECORDING\s+FEE)\b",
        v,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,;:-")

    v = re.sub(r"\s+", " ", v).strip(" ,;:-")
    words = [w for w in v.split(" ") if w]
    if len(words) > 8:
        return ""
    if len(v) > 180:
        return ""
    return v


def _county_env_key() -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(COUNTY_LABEL or "").upper()).strip("_") or "COUNTY"


def _sanitization_disabled() -> bool:
    """Whether to bypass borrower/address sanitization.

    Useful for counties where you prefer raw extraction without aggressive cleanup.
    """
    env_key = _county_env_key()
    candidates = [
        "DISABLE_SANITIZATION",
        f"{env_key}_DISABLE_SANITIZATION",
        # Friendly aliases used by some runners.
        f"{env_key.replace('_', '')}_DISABLE_SANITIZATION",
    ]
    for k in candidates:
        if str(os.getenv(k, "")).strip() == "1":
            return True
    return False


def _llm_regex_fallback_enabled() -> bool:
    """Whether to allow regex fallback even when LLM is enabled.

    Some counties benefit from hybrid extraction for property addresses when
    the LLM returns only parcel/APN or misses the street address.
    """
    env_key = _county_env_key()
    candidates = [
        "LLM_REGEX_FALLBACK",
        f"{env_key}_LLM_REGEX_FALLBACK",
        f"{env_key.replace('_', '')}_LLM_REGEX_FALLBACK",
    ]
    for k in candidates:
        if str(os.getenv(k, "")).strip() == "1":
            return True
    return False


def _maybe_sanitize_property_address(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw or raw.upper() == "NOT_FOUND":
        return ""

    if _sanitization_disabled():
        # Lenient cleanup: keep the address fragment, but do not apply strict
        # word/length caps that may blank valid rural addresses.
        frag = _extract_relevant_address_fragment(raw) or raw
        frag = re.sub(
            r"^(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as|property\s+located\s+at|located\s+at)\s*[:\-]\s*",
            "",
            frag,
            flags=re.I,
        ).strip(" ,;:-")
        frag = re.split(
            r"\b(?:APN|ASSESSOR(?:'S)?\s+PARCEL|REQUESTED\s+BY|WHEN\s+RECORDED|TOGETHER\s+WITH|THIS\s+SECURITY\s+INSTRUMENT|LEGAL\s+DESCRIPTION|RECORDING\s+FEE)\b",
            frag,
            maxsplit=1,
            flags=re.I,
        )[0].strip(" ,;:-")
        return re.sub(r"\s+", " ", frag).strip(" ,;:-")
    return sanitize_property_address(value)


def _address_quality_score(value: str) -> int:
    v = str(value or "")
    if not v:
        return -1
    score = 0
    if re.search(r"^PARCEL\s+ID\s+", v, re.I):
        score += 2
    if re.search(r"\b\d{1,6}\b", v):
        score += 3
    if _ADDRESS_STREET_SUFFIX_RE.search(v):
        score += 4
    if re.search(r",\s*[A-Z][A-Za-z .'-]+(?:,\s*AZ\b)?", v):
        score += 1
    return score


def _choose_best_property_address(*candidates: str) -> str:
    best = ""
    best_score = -1
    for raw in candidates:
        cleaned = _maybe_sanitize_property_address(raw)
        if not cleaned:
            continue
        score = _address_quality_score(cleaned)
        if score > best_score:
            best = cleaned
            best_score = score
    return best


def _regex_address(text: str) -> str:
    exclude = [
        r"\bDEED OF TRUST\b",
        r"\bLegal Lot Block\b",
        r"Section.*Township",
        r"0\s+Items\s+in\s+Cart",
        r"\bSign In\b",
        r"County,\s*AZ\s*Record",
        r"theCountyRecorder\.com",
        r"County Selection Missing",
        r"Skip Navigation Links",
        r"Requested By",
        r"Recording Fee",
    ]

    def _clean_candidate(val: str) -> str:
        v = _maybe_sanitize_property_address(val)
        if not v:
            return ""
        if any(re.search(e, v, re.I) for e in exclude):
            return ""
        return v

    # Prefer explicit property labels to avoid capturing party mailing addresses.
    label_pats = [
        r"(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as|property\s+located\s+at|located\s+at)\s*[:\-]\s*(.+)",
        r"(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as)\s+(.+)",
    ]
    lines = [ln.strip() for ln in (text or "").splitlines() if ln and ln.strip()]
    for idx, line in enumerate(lines):
        for lp in label_pats:
            m = re.search(lp, line, re.I)
            if not m:
                continue
            cand = m.group(1)
            if idx + 1 < len(lines) and len(cand) < 12:
                cand = f"{cand} {lines[idx + 1]}"
            out = _clean_candidate(cand)
            if out:
                return out

    pats = [
        r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][A-Za-z0-9\s.,#\-']{3,90}\b(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|WAY|HWY|HIGHWAY|PKWY|PARKWAY|CIR|CIRCLE)\b(?:,\s*[A-Z][A-Za-z .'-]+,\s*AZ(?:\s+\d{5}(?:-\d{4})?)?)?",
    ]
    for p in pats:
        m = re.search(p, text, re.I | re.M)
        if m:
            val = m.group(1) if m.lastindex and m.group(1) else m.group(0)
            out = _clean_candidate(val)
            if out:
                return out
    return ""


def _regex_party(text: str, label: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip(" |:;,-") for ln in (text or "").splitlines()]
    stop_terms = [
        "SHOW NAME INDEXING",
        "HIDE NAME INDEXING",
        "UNDER THIS SECURITY INSTRUMENT",
        "TO THE EXTENT OF",
        "REQUESTED BY",
    ]
    label_re = re.compile(rf"\b{re.escape(label)}\b", re.I)
    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue
        cand = label_re.sub("", line).strip(" :-|,")
        if not cand and i + 1 < len(lines):
            cand = lines[i + 1]
        cand = re.sub(r"\s+", " ", cand).strip(" |:;,-")
        if not cand:
            continue
        if any(t in cand.upper() for t in stop_terms):
            continue
        if len(cand) < 4:
            continue
        if not re.search(r"[A-Za-z]", cand):
            continue
        return cand
    return ""


def _extract_party_block(text: str, role: str) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", ln).strip(" |:;,-") for ln in text.splitlines() if ln and ln.strip()]
    label_patterns = [
        rf"name\s+and\s+address\s+of\s+(?:the\s+)?{role}",
        rf"\b{role}\b",
    ]
    stop_patterns = [
        r"name\s+and\s+address\s+of",
        r"recording\s+requested\s+by",
        r"when\s+recorded\s+mail\s+to",
        r"notice\s+of",
        r"apn\b",
    ]
    for i, line in enumerate(lines):
        if not any(re.search(lp, line, re.I) for lp in label_patterns):
            continue
        candidate_parts: list[str] = []
        after = line
        for lp in label_patterns:
            after = re.sub(lp, "", after, flags=re.I)
        after = after.strip(" :-|,")
        if after:
            candidate_parts.append(after)
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j]
            if any(re.search(sp, nxt, re.I) for sp in stop_patterns):
                break
            if nxt:
                candidate_parts.append(nxt)
            if len(" ".join(candidate_parts)) > 140:
                break
        cand = re.sub(r"\s+", " ", " ".join(candidate_parts)).strip(" |:;,-")
        if not cand:
            continue
        bad = [
            "UNDER THIS SECURITY INSTRUMENT",
            "TO THE EXTENT OF",
            "SHOW NAME INDEXING",
            "HIDE NAME INDEXING",
            "NAME AND ADDRESS",
        ]
        if any(b in cand.upper() for b in bad):
            continue
        if len(cand) < 5 or not re.search(r"[A-Za-z]", cand):
            continue
        return cand
    return ""


def _groq_request(messages: list[dict[str, str]], api_key: str, timeout_s: int = 60) -> tuple[dict, str]:
    def _normalize_groq_model(model: str) -> str:
        m = (model or "").strip()
        if m == "llama-3.3-70b":
            return "llama-3.3-70b-versatile"
        return m or "llama-3.3-70b-versatile"

    model = _normalize_groq_model(os.getenv("GROQ_MODEL", "llama-3.3-70b"))
    last_err = ""
    for use_response_format in (True, False):
        body = {
            "model": model,
            "temperature": 0,
            "messages": messages,
        }
        if use_response_format:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout_s,
            )
            resp.raise_for_status()
            payload = resp.json()
            content = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.I)
            data = json.loads(content) if content else {}
            if isinstance(data, dict):
                return data, model
            last_err = "invalid JSON object"
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            resp_body = ""
            try:
                resp_body = (exc.response.text or "")[:220] if exc.response is not None else ""
            except Exception:
                resp_body = ""
            if status in (401, 403):
                msg = (
                    f"Groq access denied (HTTP {status}). "
                    "Check GROQ_API_KEY validity and network/egress policy (VPN, proxy, firewall, datacenter IP restrictions)."
                )
                if resp_body:
                    msg = f"{msg} response={resp_body}"
                raise RuntimeError(msg)
            if status in (400, 413, 422):
                last_err = f"Groq HTTP {status} bad request (model={model}); response={resp_body or str(exc)}"
            else:
                last_err = str(exc)
        except Exception as exc:
            last_err = str(exc)

    raise RuntimeError(last_err or f"Groq request failed (model={model})")


def _resolve_hosted_document_endpoint_url() -> str:
    direct = (
        os.getenv("GREENLEE_LLM_DOCUMENT_ENDPOINT_URL", "")
        or os.getenv("GROQ_LLM_DOCUMENT_ENDPOINT_URL", "")
    ).strip()
    if direct:
        return direct
    base = (
        os.getenv("GREENLEE_LLM_ENDPOINT_URL", "")
        or os.getenv("GROQ_LLM_ENDPOINT_URL", "")
    ).strip()
    if base:
        # Use configured endpoint as-is. Supports both:
        #  - /api/v1/llm/extract-document (PDF payload)
        #  - /api/v1/llm/extract (OCR text payload)
        if "/api/v1/llm/extract" in base:
            return base
        # Fallback if only host/base URL is provided.
        return base.rstrip("/") + "/api/v1/llm/extract"
    return ""


def _build_pdf_base64_from_images(
    image_urls: list[str],
    session: requests.Session,
    max_pages: int = 6,
    timeout_s: int = 30,
) -> tuple[str, int]:
    pages: list[Image.Image] = []
    for u in (image_urls or [])[:max_pages]:
        try:
            rr = session.get(u, timeout=timeout_s)
            rr.raise_for_status()
            ctype = (rr.headers.get("Content-Type") or "").lower()
            if "image" not in ctype:
                continue
            im = Image.open(io.BytesIO(rr.content))
            im.load()
            if im.mode != "RGB":
                im = im.convert("RGB")
            pages.append(im)
        except Exception:
            continue
    if not pages:
        return "", 0
    buf = io.BytesIO()
    first = pages[0]
    rest = pages[1:]
    first.save(buf, format="PDF", save_all=True, append_images=rest)
    pdf_bytes = buf.getvalue()
    if not pdf_bytes:
        return "", 0
    return base64.b64encode(pdf_bytes).decode("ascii"), len(pages)


def _hosted_extract_fields_from_document(
    *,
    endpoint_url: str,
    image_urls: list[str],
    session: requests.Session,
    recording_number: str,
    timeout_s: int = 90,
) -> tuple[dict, str, str]:
    headers = {"Content-Type": "application/json"}
    api_token = (os.getenv("GREENLEE_API_TOKEN", "") or os.getenv("API_TOKEN", "")).strip()
    if api_token:
        headers["X-API-Token"] = api_token

    # Optional model override (kept as header to avoid breaking strict JSON schemas on hosted service).
    model_override = (os.getenv("GROQ_MODEL", "") or "").strip()
    if model_override == "llama-3.3-70b":
        model_override = "llama-3.3-70b-versatile"
    if model_override:
        headers["X-LLM-Model"] = model_override

    timeout_env = (
        os.getenv("GREENLEE_LLM_ENDPOINT_TIMEOUT_S", "")
        or os.getenv("GROQ_LLM_ENDPOINT_TIMEOUT_S", "")
    ).strip()
    if timeout_env:
        try:
            timeout_s = max(10, int(float(timeout_env)))
        except Exception:
            pass

    def _map_response(data: dict, pages_used: int, ocr_text: str) -> tuple[dict, str, str]:
        fields = data.get("fields", data) if isinstance(data, dict) else {}
        if not isinstance(fields, dict):
            raise RuntimeError("hosted LLM endpoint returned invalid fields payload")

        trustor = str(fields.get("trustor") or fields.get("trustor_1_full_name") or "").strip()
        trustee = str(fields.get("trustee") or "").strip()
        beneficiary = str(fields.get("beneficiary") or "").strip()
        property_address_raw = str(fields.get("propertyAddress") or fields.get("property_address") or "").strip()
        property_address = _maybe_sanitize_property_address(property_address_raw) or ""
        principal_amount_raw = str(fields.get("principalAmount") or fields.get("original_principal_balance") or "").strip()
        principal_amount = _normalize_principal_amount_numeric(principal_amount_raw) or ""

        mapped = {
            "trustor": trustor,
            "trustee": trustee,
            "beneficiary": beneficiary,
            "propertyAddress": property_address,
            "principalAmount": principal_amount,
        }
        model = str(data.get("model") or "hosted-llm-endpoint").strip() if isinstance(data, dict) else "hosted-llm-endpoint"
        if pages_used > 0:
            model = f"{model} pages={pages_used}"
        return mapped, model, ocr_text

    # Mode A: document endpoint accepts PDF payload.
    if "/extract-document" in endpoint_url:
        pdf_base64, pages_used = _build_pdf_base64_from_images(image_urls=image_urls, session=session)
        if not pdf_base64:
            raise RuntimeError("hosted LLM endpoint: unable to build PDF payload from image URLs")
        payload = {
            "pdf_base64": pdf_base64,
            "fallback_to_rule_based": True,
            "recording_number": str(recording_number or ""),
        }
        rr = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout_s)
        if rr.status_code < 400:
            data = rr.json() if rr.content else {}
            ocr_text = str(data.get("ocr_text") or "") if isinstance(data, dict) else ""
            return _map_response(data, pages_used, ocr_text)

        # Auto-fallback to OCR-text endpoint style when document route is unavailable.
        endpoint_url = endpoint_url.replace("/extract-document", "/extract")

    # Mode B: text endpoint accepts OCR text payload.
    ocr_text, _ = ocr_document_images(image_urls, session, max_pages=6)
    if not ocr_text.strip():
        raise RuntimeError("hosted LLM endpoint fallback: OCR text unavailable")
    payload = {
        "ocr_text": ocr_text,
        "recording_number": str(recording_number or ""),
    }
    rr = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout_s)
    if rr.status_code >= 400:
        body = ""
        try:
            body = (rr.text or "")[:260]
        except Exception:
            body = ""
        raise RuntimeError(f"hosted LLM endpoint HTTP {rr.status_code}; response={body}")

    data = rr.json() if rr.content else {}
    return _map_response(data, 0, ocr_text)


def _hosted_extract_fields_from_text(
    *,
    endpoint_url: str,
    text: str,
    recording_number: str,
    timeout_s: int = 60,
) -> tuple[dict, str]:
    """Call hosted /extract endpoint with OCR-like text (can be detail/rawText when images are blocked)."""
    headers = {"Content-Type": "application/json"}
    api_token = (os.getenv("GREENLEE_API_TOKEN", "") or os.getenv("API_TOKEN", "")).strip()
    if api_token:
        headers["X-API-Token"] = api_token

    model_override = (os.getenv("GROQ_MODEL", "") or "").strip()
    if model_override == "llama-3.3-70b":
        model_override = "llama-3.3-70b-versatile"
    if model_override:
        headers["X-LLM-Model"] = model_override

    timeout_env = (
        os.getenv("GREENLEE_LLM_ENDPOINT_TIMEOUT_S", "")
        or os.getenv("GROQ_LLM_ENDPOINT_TIMEOUT_S", "")
    ).strip()
    if timeout_env:
        try:
            timeout_s = max(10, int(float(timeout_env)))
        except Exception:
            pass

    url = (endpoint_url or "").strip()
    if not url:
        raise RuntimeError("hosted LLM endpoint URL missing")

    # Ensure we hit the text endpoint.
    if "/extract-document" in url:
        url = url.replace("/extract-document", "/extract")

    ocr_text = str(text or "")
    if not ocr_text.strip():
        raise RuntimeError("hosted LLM endpoint: text payload is empty")

    payload = {
        "ocr_text": ocr_text,
        "recording_number": str(recording_number or ""),
    }
    rr = requests.post(url, json=payload, headers=headers, timeout=timeout_s)
    if rr.status_code >= 400:
        body = ""
        try:
            body = (rr.text or "")[:260]
        except Exception:
            body = ""
        raise RuntimeError(f"hosted LLM endpoint HTTP {rr.status_code}; response={body}")

    data = rr.json() if rr.content else {}
    fields = data.get("fields", data) if isinstance(data, dict) else {}
    if not isinstance(fields, dict):
        raise RuntimeError("hosted LLM endpoint returned invalid fields payload")

    trustor = str(fields.get("trustor") or fields.get("trustor_1_full_name") or "").strip()
    trustee = str(fields.get("trustee") or "").strip()
    beneficiary = str(fields.get("beneficiary") or "").strip()
    property_address_raw = str(fields.get("propertyAddress") or fields.get("property_address") or "").strip()
    property_address = _maybe_sanitize_property_address(property_address_raw) or ""
    principal_amount_raw = str(fields.get("principalAmount") or fields.get("original_principal_balance") or "").strip()
    principal_amount = _normalize_principal_amount_numeric(principal_amount_raw) or ""

    mapped = {
        "trustor": trustor,
        "trustee": trustee,
        "beneficiary": beneficiary,
        "propertyAddress": property_address,
        "principalAmount": principal_amount,
    }
    model = str(data.get("model") or "hosted-llm-endpoint").strip() if isinstance(data, dict) else "hosted-llm-endpoint"
    return mapped, model


def _normalise_party(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "")).strip(" |:;,-")


def _first_party(parties: str) -> str:
    vals = [
        _normalise_party(x)
        for x in str(parties or "").split("|")
        if _normalise_party(x)
    ]
    return vals[0] if vals else ""


def _looks_bad_party(v: str) -> bool:
    u = (v or "").upper()
    if not u:
        return True
    bad = [
        "UNDER THIS SECURITY INSTRUMENT",
        "TO THE EXTENT OF",
        "SHOW NAME INDEXING",
        "HIDE NAME INDEXING",
        "NAME AND ADDRESS",
    ]
    return any(b in u for b in bad)


_BORROWER_NOISE_PATTERNS = [
    r"\bSHOW\s+NAME\s+INDEXING\b",
    r"\bHIDE\s+NAME\s+INDEXING\b",
    r"\bUNDER\s+THIS\s+SECURITY\s+INSTRUMENT\b",
    r"\bTO\s+THE\s+EXTENT\s+OF\b",
    r"\bNAME\s+AND\s+ADDRESS\b",
    r"\bREQUESTED\s+BY\b",
    r"\bWHEN\s+RECORDED\s+MAIL\s+TO\b",
    r"\bCOUNTY\s+SELECTION\s+MISSING\b",
    r"\bSKIP\s+NAVIGATION\s+LINKS\b",
]


_BORROWER_SPLIT_RE = re.compile(
    r"\s*(?:,\s*and\s+|\band\s+|\bas\b|;|\||/|\baka\b|\bdba\b|\bfka\b|\bet\s+al\b)\s*",
    re.I,
)


_BUSINESS_SUFFIX_RE = re.compile(
    r"\b(LLC|L\.L\.C\.|INC|INC\.|CORP|CORPORATION|CO\.|COMPANY|LIMITED|LTD|PLC|LP|LLP|BANK|ASSOCIATION|TRUST)\b",
    re.I,
)


def sanitize_borrower_name(value: str) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip(" |:;,-")
    if not v:
        return ""

    # Remove role labels if OCR/LLM returned them inline.
    v = re.sub(r"^(?:trustor|borrower|mortgagor)\s*[:\-]\s*", "", v, flags=re.I).strip(" |:;,-")
    # If OCR concatenated multiple labels, keep first segment.
    v = re.split(r"\b(?:trustee|beneficiary|requested\s+by|when\s+recorded\s+mail\s+to)\b", v, maxsplit=1, flags=re.I)[0].strip(" |:;,-")
    # Drop leading legal-descriptor boilerplate and keep the actual name/entity.
    v = re.sub(
        r"^A\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,4}\s+LIMITED\s+LIABILITY\s+COMPANY\b\s*(?:AS)?\s*,?\s*",
        "",
        v,
        flags=re.I,
    ).strip(" |:;,-")
    v = re.sub(r"^AN?\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,4}\s+CORPORATION\b\s*(?:AS)?\s*,?\s*", "", v, flags=re.I).strip(" |:;,-")
    v = re.sub(r"^(?:AND|,)\s*", "", v, flags=re.I).strip(" |:;,-")
    # Strip trailing embedded address fragments from party names.
    v = re.split(
        r"\b\d{1,6}\s+(?:[NSEW]\.?(?:\s+|$))?[A-Z0-9][A-Za-z0-9\s.#\-']{2,80}\b"
        r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|WAY|HWY|HIGHWAY|PKWY|PARKWAY|CIR|CIRCLE|TRL|TRAIL)\b",
        v,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" |:;,-")

    # Keep only the first relevant entity if multiple names/entities are concatenated.
    parts = [p.strip(" ,;:-") for p in _BORROWER_SPLIT_RE.split(v) if p and p.strip(" ,;:-")]
    if parts:
        v = parts[0]

    # If legal descriptor is appended, cut at descriptor start.
    v = re.split(r"\bA\s+(?:AN\s+)?[A-Z][A-Za-z]+\s+LIMITED\s+LIABILITY\s+COMPANY\b", v, maxsplit=1, flags=re.I)[0].strip(" ,;:-")

    # Keep up to business suffix when present.
    m_suffix = _BUSINESS_SUFFIX_RE.search(v)
    if m_suffix:
        v = v[: m_suffix.end()].strip(" ,;:-")
    else:
        # Person/other names: keep compact first 4 words.
        words = [w for w in re.split(r"\s+", v) if w]
        if len(words) > 4:
            v = " ".join(words[:4])

    v = re.sub(r"\s*,\s*", " ", v)
    v = re.sub(r"\s+", " ", v).strip(" |:;,-")

    if not v or len(v) < 2 or len(v) > 140:
        return ""
    vu = v.upper()
    if vu in {"UNKNOWN", "N/A", "NA", "NULL", "-", "THIS DEED OF TRUST", "DEED OF TRUST", "THIS INSTRUMENT"}:
        return ""
    if re.search(r"\bTHIS\s+DEED\s+OF\s+TRUST\b", vu):
        return ""
    if not re.search(r"[A-Za-z]", v):
        return ""

    return v


def _maybe_sanitize_borrower_name(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip(" |:;,-")
    if not raw or raw.upper() == "NOT_FOUND":
        return ""
    if _sanitization_disabled():
        # Lenient cleanup: avoid truncation/splitting, but remove obvious role labels.
        raw = re.sub(r"^(?:trustor|borrower|mortgagor)\s*[:\-]\s*", "", raw, flags=re.I).strip(" |:;,-")
        return raw
    return sanitize_borrower_name(value)


def _borrower_quality_score(value: str) -> int:
    v = str(value or "")
    if not v:
        return -1
    score = 0
    words = [w for w in re.split(r"\s+", v) if w]
    score += min(len(words), 5)
    if re.search(r"\b(LLC|INC|BANK|CORP|TRUST|ASSOCIATION|TOWN|CITY|COUNTY)\b", v, re.I):
        score += 1
    if re.search(r"\b(TRUSTEE|BENEFICIARY)\b", v, re.I):
        score -= 2
    return score


def _choose_best_borrower_name(*candidates: str) -> str:
    best = ""
    best_score = -1
    for raw in candidates:
        cleaned = _maybe_sanitize_borrower_name(raw)
        if not cleaned:
            continue
        score = _borrower_quality_score(cleaned)
        if score > best_score:
            best = cleaned
            best_score = score
    return best


def _safe_filtered_party(value: str) -> str:
    """Return filtered party name when valid, otherwise keep trimmed original."""
    raw = _normalise_party(value)
    clean = _maybe_sanitize_borrower_name(raw)
    return clean or raw


def _groq_extract_fields(
    *,
    document_id: str,
    recording_number: str,
    document_type: str,
    ocr_text: str,
    detail_text: str,
    api_key: str,
) -> tuple[dict, str]:
    # Keep payload bounded to reduce Groq 400/context-limit errors on very large OCR dumps.
    max_ocr_chars = 18000
    max_detail_chars = 8000
    ocr_text = (ocr_text or "")[:max_ocr_chars]
    detail_text = (detail_text or "")[:max_detail_chars]

    system_prompt = COUNTY_LLM_SYSTEM_PROMPT
    user_payload = {
        "documentId": document_id,
        "recordingNumber": recording_number,
        "documentType": document_type,
        "ocrText": (ocr_text or "")[:6000],
        "detailText": (detail_text or "")[:2500],
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    data, model = _groq_request(messages, api_key=api_key, timeout_s=70)
    return data, model


def enrich_record(
    record: dict,
    session: requests.Session,
    use_groq: bool = True,
    groq_api_key: str = "",
    max_image_pages: int = 6,
) -> dict:
    dk = record.get("documentId", "")
    if not dk:
        return record
    try:
        detail = fetch_detail(dk, session)
    except Exception as e:
        record["analysisError"] = f"detail fetch failed: {e}"
        return record

    detail_address = _maybe_sanitize_property_address(detail.get("propertyAddress", ""))
    if detail_address:
        detail["propertyAddress"] = detail_address
    detail_trustor = _maybe_sanitize_borrower_name(detail.get("trustor", ""))
    if detail_trustor:
        detail["trustor"] = detail_trustor

    for key in [
        "detailUrl",
        "recordingNumber",
        "recordingDate",
        "documentType",
        "grantors",
        "grantees",
        "trustor",
        "trustee",
        "beneficiary",
        "propertyAddress",
        "principalAmount",
    ]:
        if detail.get(key):
            record[key] = detail[key]
    image_urls = discover_image_urls(dk, session, detail.get("imageUrls", []), max_probe_pages=max_image_pages)

    # Absolute, clickable links for downstream storage/export.
    detail_url = str(record.get("detailUrl") or detail.get("detailUrl") or f"{DOCUMENT_URL}?DK={dk}")
    detail_url = urllib.parse.urljoin(BASE_URL + "/", detail_url)
    record["detailUrl"] = detail_url

    norm_image_urls: list[str] = []
    for u in image_urls:
        uu = urllib.parse.urljoin(BASE_URL + "/", str(u or ""))
        if uu and uu not in norm_image_urls:
            norm_image_urls.append(uu)
    image_urls = norm_image_urls

    record["documentUrls"] = " | ".join([detail_url] + image_urls)
    record["links"] = json.dumps(
        {
            "document_url": detail_url,
            "image_urls": image_urls,
        },
        ensure_ascii=False,
    )
    record["imageUrls"] = " | ".join(image_urls)
    hosted_endpoint_url = _resolve_hosted_document_endpoint_url()
    disable_local_ocr = str(os.getenv("GREENLEE_DISABLE_LOCAL_OCR", "0")).strip() == "1"
    llm_enabled = bool(use_groq and (hosted_endpoint_url or groq_api_key))

    # If hosted endpoint is configured, bypass local OCR and send document pages directly.
    if use_groq and hosted_endpoint_url and image_urls:
        try:
            llm, model, hosted_ocr_text = _hosted_extract_fields_from_document(
                endpoint_url=hosted_endpoint_url,
                image_urls=image_urls,
                session=session,
                recording_number=record.get("recordingNumber", ""),
            )
            record["usedGroq"] = True
            record["groqModel"] = model
            record["ocrMethod"] = "hosted-document-endpoint"
            record["ocrChars"] = len(hosted_ocr_text or "")

            for key in ["trustor", "trustee", "beneficiary", "propertyAddress", "principalAmount"]:
                llm_val = (llm.get(key) or "").strip()
                if key == "trustor":
                    llm_val = _maybe_sanitize_borrower_name(llm_val)
                if key == "propertyAddress":
                    llm_val = _maybe_sanitize_property_address(llm_val)
                if llm_val:
                    record[key] = llm_val

            # LLM-only mode: do not use regex-derived extraction/fallback.
            if not record.get("trustor"):
                record["trustor"] = _first_party(record.get("grantors", ""))
            if not record.get("beneficiary"):
                record["beneficiary"] = _first_party(record.get("grantees", ""))

            record["trustor"] = _choose_best_borrower_name(
                detail_trustor,
                record.get("trustor", ""),
                _first_party(record.get("grantors", "")),
                "",
                "",
            )
            record["trustee"] = _safe_filtered_party(record.get("trustee", ""))
            record["beneficiary"] = _safe_filtered_party(record.get("beneficiary", ""))
            regex_addr = ""
            if _llm_regex_fallback_enabled():
                merged_for_addr = (str(hosted_ocr_text or "") + "\n" + str(detail.get("rawText", "") or "")).strip()
                regex_addr = _regex_address(merged_for_addr)
            record["propertyAddress"] = _choose_best_property_address(
                detail_address,
                record.get("propertyAddress", ""),
                regex_addr,
            )

            manual, reasons, summary, context = _compute_manual_review(record, merged_text=detail.get("rawText", ""))
            record["manualReview"] = manual
            record["manualReviewReasons"] = reasons
            record["manualReviewSummary"] = summary
            record["manualReviewContext"] = context
            return record
        except Exception as e:
            record.setdefault("groqError", "")
            record["groqError"] = str(e)
            if disable_local_ocr:
                ocr_text = ""
                ocr_method = "skipped-local-ocr"
                blocked_no_image = False
                record["ocrMethod"] = ocr_method
                record["ocrChars"] = 0
                record.setdefault("usedGroq", False)
                record.setdefault("groqModel", "")
                merged = (ocr_text + "\n" + detail.get("rawText", "")).strip()

                if use_groq and groq_api_key and (ocr_text.strip() or detail.get("rawText", "").strip()):
                    try:
                        llm, model = _groq_extract_fields(
                            document_id=dk,
                            recording_number=record.get("recordingNumber", ""),
                            document_type=record.get("documentType", ""),
                            ocr_text=ocr_text,
                            detail_text=detail.get("rawText", ""),
                            api_key=groq_api_key,
                        )
                        record["usedGroq"] = True
                        record["groqModel"] = model
                        for key in ["trustor", "trustee", "beneficiary", "propertyAddress", "principalAmount"]:
                            llm_val = (llm.get(key) or "").strip()
                            if key == "trustor":
                                llm_val = _maybe_sanitize_borrower_name(llm_val)
                            if key == "propertyAddress":
                                llm_val = _maybe_sanitize_property_address(llm_val)
                            if llm_val:
                                record[key] = llm_val
                    except Exception as inner_e:
                        record["groqError"] = str(inner_e)

                if not record.get("trustor"):
                    record["trustor"] = _first_party(record.get("grantors", ""))
                if not record.get("beneficiary"):
                    record["beneficiary"] = _first_party(record.get("grantees", ""))
                record["trustor"] = _choose_best_borrower_name(
                    detail_trustor,
                    record.get("trustor", ""),
                    _first_party(record.get("grantors", "")),
                    "",
                    "",
                )
                record["trustee"] = _safe_filtered_party(record.get("trustee", ""))
                record["beneficiary"] = _safe_filtered_party(record.get("beneficiary", ""))
                record["propertyAddress"] = _choose_best_property_address(
                    detail_address,
                    record.get("propertyAddress", ""),
                )

                manual, reasons, summary, context = _compute_manual_review(
                    record,
                    merged_text=(ocr_text + "\n" + str(detail.get("rawText", "") or "")).strip(),
                )
                record["manualReview"] = manual
                record["manualReviewReasons"] = reasons
                record["manualReviewSummary"] = summary
                record["manualReviewContext"] = context
                return record

    if disable_local_ocr:
        ocr_text, ocr_method = "", "skipped-local-ocr"
    else:
        ocr_text, ocr_method = ocr_document_images(image_urls, session, max_pages=max_image_pages)
    blocked_no_image = False
    if not image_urls and detail.get("imageAccessNote"):
        note = detail.get("imageAccessNote", "")
        note_l = note.lower()
        record["analysisError"] = note
        blocked_no_image = True
        if "unofficial images" in note_l:
            ocr_method = "unavailable-county-blocked"
        elif "not perfected" in note_l:
            ocr_method = "unavailable-not-perfected"
        else:
            ocr_method = "unavailable-no-images"
    record["ocrMethod"] = ocr_method
    record["ocrChars"] = len(ocr_text)
    record.setdefault("usedGroq", False)
    record.setdefault("groqModel", "")
    record.setdefault("groqError", "")
    merged = (ocr_text + "\n" + detail.get("rawText", "")).strip()

    # If the county blocks unofficial images (no OCR possible), still attempt hosted LLM extraction
    # using the detail page text so we preserve an LLM-only pipeline (no regex fallback).
    if (
        llm_enabled
        and hosted_endpoint_url
        and blocked_no_image
        and str(detail.get("rawText", "") or "").strip()
        and not record.get("usedGroq")
    ):
        try:
            llm, model = _hosted_extract_fields_from_text(
                endpoint_url=hosted_endpoint_url,
                text=detail.get("rawText", ""),
                recording_number=record.get("recordingNumber", ""),
            )
            record["usedGroq"] = True
            record["groqModel"] = model
            record["ocrMethod"] = "hosted-text-endpoint"
            record["ocrChars"] = len(str(detail.get("rawText", "") or ""))

            for key in ["trustor", "trustee", "beneficiary", "propertyAddress", "principalAmount"]:
                llm_val = (llm.get(key) or "").strip()
                if key == "trustor":
                    llm_val = _maybe_sanitize_borrower_name(llm_val)
                if key == "propertyAddress":
                    llm_val = _maybe_sanitize_property_address(llm_val)
                if llm_val:
                    record[key] = llm_val
        except Exception as e:
            record["groqError"] = str(e)

    if use_groq and groq_api_key and not blocked_no_image and (ocr_text.strip() or detail.get("rawText", "").strip()):
        try:
            llm, model = _groq_extract_fields(
                document_id=dk,
                recording_number=record.get("recordingNumber", ""),
                document_type=record.get("documentType", ""),
                ocr_text=ocr_text,
                detail_text=detail.get("rawText", ""),
                api_key=groq_api_key,
            )
            record["usedGroq"] = True
            record["groqModel"] = model

            # LLM-first mapping (no regex dependency when LLM is enabled).
            for key in ["trustor", "trustee", "beneficiary", "propertyAddress", "principalAmount"]:
                llm_val = (llm.get(key) or "").strip()
                if key == "propertyAddress":
                    llm_val = _maybe_sanitize_property_address(llm_val)
                if key == "trustor":
                    llm_val = _maybe_sanitize_borrower_name(llm_val)
                if llm_val:
                    record[key] = llm_val

            llm_grantors = llm.get("grantors") or []
            llm_grantees = llm.get("grantees") or []
            if isinstance(llm_grantors, list) and llm_grantors:
                record["grantors"] = " | ".join(_normalise_party(x) for x in llm_grantors if str(x).strip())
            if isinstance(llm_grantees, list) and llm_grantees:
                record["grantees"] = " | ".join(_normalise_party(x) for x in llm_grantees if str(x).strip())

            # Optional hybrid fallback for address when LLM misses street address.
            if _llm_regex_fallback_enabled():
                if not str(record.get("propertyAddress", "") or "").strip() or str(record.get("propertyAddress", "")).strip().upper() == "NOT_FOUND":
                    record["propertyAddress"] = _regex_address(merged) or record.get("propertyAddress", "")
        except Exception as e:
            record["groqError"] = str(e)

    if not llm_enabled:
        # Regex fallback path only when LLM is unavailable/disabled.
        if not record.get("principalAmount"):
            record["principalAmount"] = _regex_principal(merged)
        if not record.get("propertyAddress"):
            record["propertyAddress"] = _regex_address(merged)
        for label, key in [("trustor", "trustor"), ("trustee", "trustee"), ("beneficiary", "beneficiary")]:
            if not record.get(key):
                record[key] = _extract_party_block(ocr_text, label)
            if not record.get(key):
                record[key] = _extract_party_block(merged, label)
            if not record.get(key):
                record[key] = _regex_party(merged, label)
        if not record.get("trustor"):
            record["trustor"] = _first_party(record.get("grantors", ""))
        if not record.get("beneficiary"):
            record["beneficiary"] = _first_party(record.get("grantees", ""))

    if llm_enabled:
        record["trustor"] = _choose_best_borrower_name(
            detail_trustor,
            record.get("trustor", ""),
            _first_party(record.get("grantors", "")),
            "",
            "",
        )
    else:
        record["trustor"] = _choose_best_borrower_name(
            detail_trustor,
            record.get("trustor", ""),
            _first_party(record.get("grantors", "")),
            _extract_party_block(merged, "trustor"),
            _regex_party(merged, "trustor"),
        )
    record["trustee"] = _safe_filtered_party(record.get("trustee", ""))
    record["beneficiary"] = _safe_filtered_party(record.get("beneficiary", ""))
    if llm_enabled:
        record["propertyAddress"] = _choose_best_property_address(
            detail_address,
            record.get("propertyAddress", ""),
        )
    else:
        record["propertyAddress"] = _choose_best_property_address(
            detail_address,
            record.get("propertyAddress", ""),
            _regex_address(merged),
        )

    # Optional hybrid fallback: include regex address even when LLM is enabled.
    if llm_enabled and _llm_regex_fallback_enabled():
        record["propertyAddress"] = _choose_best_property_address(
            detail_address,
            record.get("propertyAddress", ""),
            _regex_address(merged),
        )

    # Enforce final output constraints.
    if _strict_valuation_disabled():
        record["principalAmount"] = str(record.get("principalAmount", "") or "").strip() or "NOT_FOUND"
    else:
        record["principalAmount"] = _normalize_principal_amount_numeric(record.get("principalAmount", "")) or "NOT_FOUND"

    # Santa Cruz: parcel-id-only "addresses" are not usable as property addresses.
    if str(COUNTY_LABEL or "").strip().upper() == "SANTA CRUZ":
        pa = str(record.get("propertyAddress", "") or "")
        if re.match(r"^\s*parcel\s*id\b", pa, flags=re.I):
            record["propertyAddress"] = ""

    if _sanitization_disabled():
        record["propertyAddress"] = re.sub(r"\s+", " ", str(record.get("propertyAddress", "") or "")).strip() or "NOT_FOUND"
    else:
        record["propertyAddress"] = sanitize_property_address(record.get("propertyAddress", "")) or "NOT_FOUND"
    for key in ["trustor", "trustee", "beneficiary"]:
        v = str(record.get(key, "") or "").strip()
        record[key] = v if v else "NOT_FOUND"

    if blocked_no_image and record.get("principalAmount") in ("", "NOT_FOUND") and record.get("propertyAddress") in ("", "NOT_FOUND"):
        record["analysisError"] = (
            f"{detail.get('imageAccessNote', 'Image unavailable')}; "
            "detail page does not expose property address/principal amount"
        )

    manual, reasons, summary, context = _compute_manual_review(record, merged_text=merged)
    record["manualReview"] = manual
    record["manualReviewReasons"] = reasons
    record["manualReviewSummary"] = summary
    record["manualReviewContext"] = context
    return record


def export_csv(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def export_json(records: list[dict], out_path: Path, meta: dict | None = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _jsonable(v: Any) -> Any:
        if v is None:
            return ""
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, set):
            return [str(x) for x in sorted(v, key=lambda x: str(x))]
        if isinstance(v, tuple):
            return [_jsonable(x) for x in v]
        if isinstance(v, list):
            return [_jsonable(x) for x in v]
        if isinstance(v, dict):
            # Ensure nested dict is JSON-serializable.
            return {str(k): _jsonable(val) for k, val in v.items()}
        return str(v)

    payload = {
        "meta": meta or {},
        "count": len(records),
        "records": [{k: _jsonable(r.get(k, "")) for k in CSV_FIELDS} for r in records],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_greenlee_pipeline(
    start_date: str,
    end_date: str,
    doc_types: list[str] | None = None,
    max_pages: int = 0,
    ocr_limit: int = 10,
    workers: int = 3,
    use_groq: bool = True,
    headless: bool = True,
    verbose: bool = False,
    write_output_files: bool | None = None,
) -> dict:
    doc_types = doc_types or DEFAULT_DOCUMENT_TYPES
    cookie_header, records = playwright_collect_results(
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        max_pages=max_pages,
        headless=headless,
        verbose=verbose,
    )
    _load_local_env()
    session = _make_session(cookie_header)
    groq_key = os.getenv("GROQ_API_KEY", "")
    hosted_endpoint_url = _resolve_hosted_document_endpoint_url()
    use_groq = bool(use_groq and (groq_key or hosted_endpoint_url))
    if ocr_limit < 0:
        enrich_count = 0
    elif ocr_limit == 0:
        enrich_count = len(records)
    else:
        enrich_count = min(ocr_limit, len(records))

    # Run OCR/enrichment in parallel workers (default: 3).
    if enrich_count > 0:
        max_workers = max(1, int(workers or 1))

        def _enrich_one(idx: int) -> tuple[int, dict]:
            rec = records[idx]
            if verbose:
                print(f"[ENRICH] {idx + 1}/{len(records)} DK={rec.get('documentId','')}")
            # Use one requests session per worker task to avoid cross-thread session mutation.
            local_session = _make_session(cookie_header)
            out = enrich_record(rec, local_session, use_groq=use_groq, groq_api_key=groq_key)
            return idx, out

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_enrich_one, i) for i in range(enrich_count)]
            for fut in as_completed(futures):
                idx, out = fut.result()
                records[idx] = out

    for i, rec in enumerate(records, 1):
        if i <= enrich_count:
            continue
        try:
            detail = fetch_detail(rec.get("documentId", ""), session)
            for key in [
                "detailUrl",
                "recordingNumber",
                "recordingDate",
                "documentType",
                "grantors",
                "grantees",
                "trustor",
                "trustee",
                "beneficiary",
                "propertyAddress",
                "principalAmount",
            ]:
                if detail.get(key):
                    rec[key] = detail[key]

            # Ensure link fields are present even when OCR/enrichment is skipped.
            detail_url = str(rec.get("detailUrl") or detail.get("detailUrl") or "")
            detail_url = urllib.parse.urljoin(BASE_URL + "/", detail_url)
            rec["detailUrl"] = detail_url
            image_urls = [urllib.parse.urljoin(BASE_URL + "/", str(u or "")) for u in (detail.get("imageUrls") or [])]
            image_urls = [u for u in image_urls if u]
            rec["imageUrls"] = " | ".join(image_urls)
            rec["documentUrls"] = " | ".join([detail_url] + image_urls)
            rec["links"] = json.dumps(
                {
                    "document_url": detail_url,
                    "image_urls": image_urls,
                },
                ensure_ascii=False,
            )

            manual, reasons, summary, context = _compute_manual_review(rec, merged_text=detail.get("rawText", ""))
            rec["manualReview"] = manual
            rec["manualReviewReasons"] = reasons
            rec["manualReviewSummary"] = summary
            rec["manualReviewContext"] = context
        except Exception as e:
            rec["analysisError"] = f"detail fetch failed: {e}"
            manual, reasons, summary, context = _compute_manual_review(rec, merged_text="")
            rec["manualReview"] = manual
            rec["manualReviewReasons"] = reasons
            rec["manualReviewSummary"] = summary
            rec["manualReviewContext"] = context
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"greenlee_leads_{ts}.csv"
    json_path = OUTPUT_DIR / f"greenlee_leads_{ts}.json"
    meta = {
        "county": "Greenlee County, AZ",
        "platform": "TheCountyRecorder (ASP.NET WebForms)",
        "baseUrl": BASE_URL,
        "startDate": _normalise_date(start_date),
        "endDate": _normalise_date(end_date),
        "documentTypes": doc_types,
        "recordsFound": len(records),
        "recordsOCR": enrich_count,
        "workers": max(1, int(workers or 1)),
        "usedGroq": use_groq,
        "timestamp": datetime.now().isoformat(),
    }

    if write_output_files is None:
        write_output_files = os.getenv("WRITE_OUTPUT_FILES", "true").strip().lower() == "true"

    if write_output_files:
        export_csv(records, csv_path)
        export_json(records, json_path, meta=meta)
    else:
        csv_path = Path("")
        json_path = Path("")

    return {
        "records": records,
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "summary": meta,
    }


# Backward-compat alias
run_lapaz_pipeline = run_greenlee_pipeline
