"""LLM-based field extraction using Groq (llama-3.3-70b-versatile).

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


def _normalize_groq_model(model: str) -> str:
    m = (model or "").strip()
    # Users often shorten this; Groq expects the full model id.
    if m == "llama-3.3-70b":
        return "llama-3.3-70b-versatile"
    return m or "llama-3.3-70b-versatile"


# Enforce a single model everywhere. Allow override via GROQ_MODEL.
_MODEL = _normalize_groq_model(os.environ.get("GROQ_MODEL", "llama-3.3-70b"))

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
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
"""


_BAD_ADDRESS_PHRASES_RE = re.compile(
    r"location\s+of\s+the\s+real\s+property\s+described\s+above\s+is\s+purported\s+to\s+be"
    r"|other\s+common\s+designation"
    r"|described\s+above\s+is\s+purported"
    r"|purported\s+to\s+be",
    re.I,
)


def _validate_property_address(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n,;:-")
    if not s:
        return None
    if _BAD_ADDRESS_PHRASES_RE.search(s):
        return None
    # Must look like a street line, not a sentence.
    if not re.match(r"^\d{1,6}\b", s):
        return None
    # Hard cap: 8 words max.
    if len([w for w in s.split(" ") if w]) > 8:
        return None
    return s

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

_MAX_OCR_CHARS = int(os.environ.get("GROQ_MAX_OCR_CHARS", "24000"))

# Note: the 70B model supports a larger context; tune GROQ_MAX_OCR_CHARS as needed.


_NAME_ROLE_CUT_RE = re.compile(
    r"\b(?:A\s+SINGLE\s+WOMAN|A\s+SINGLE\s+MAN|MARRIED\s+WOMAN|MARRIED\s+MAN|HUSBAND\s+AND\s+WIFE|WIFE\s+AND\s+HUSBAND|AS\s+JOINT\s+TENANTS|AS\s+TRUSTEE(?:S)?|AKA|FKA|DBA|ET\s+AL)\b",
    re.I,
)


def _clean_person_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    s = re.sub(r"\s+", " ", str(name)).strip(" ,;:-")
    if not s:
        return None
    # Split out descriptor tail.
    s = _NAME_ROLE_CUT_RE.split(s, maxsplit=1)[0].strip(" ,;:-")
    # Remove leading conjunctions.
    s = re.sub(r"^(?:AND|OR)\s+", "", s, flags=re.I).strip(" ,;:-")
    # Remove trailing article left by descriptor removal (e.g., ", A").
    s = re.sub(r",?\s+(?:A|AN)$", "", s, flags=re.I).strip(" ,;:-")
    if not s or len(s) < 2:
        return None
    # Enforce max 4 words.
    words = [w for w in re.split(r"\s+", s) if w]
    if len(words) > 4:
        s = " ".join(words[:4])
    return s


def _split_two_trustors(primary: Optional[str], secondary: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    raw_primary = re.sub(r"\s+", " ", str(primary or "")).strip()
    p = _clean_person_name(raw_primary)
    s = _clean_person_name(secondary)
    if raw_primary and not s:
        # Handle combined string before descriptor stripping: "NAME1 ... AND NAME2 ..."
        parts = re.split(r"\s*(?:,\s*and\s+|\band\b|&)\s*", raw_primary, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            p1 = _clean_person_name(parts[0])
            p2 = _clean_person_name(parts[1])
            if p1 and p2:
                return p1, p2
    return p, s


def _name_parts(full_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    n = _clean_person_name(full_name)
    if not n:
        return None, None
    toks = [t for t in re.split(r"\s+", n) if t]
    if not toks:
        return None, None
    if len(toks) == 1:
        return toks[0], None
    return toks[0], toks[-1]


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

    return _map_fields_dict(fields)


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
                max_tokens=int(os.environ.get("GROQ_MAX_TOKENS", "4096")),
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

    t1_full, t2_full = _split_two_trustors(
        _str_or_none("trustor_1_full_name"),
        _str_or_none("trustor_2_full_name"),
    )
    t1_first_raw = _clean_person_name(_str_or_none("trustor_1_first_name"))
    t1_last_raw = _clean_person_name(_str_or_none("trustor_1_last_name"))
    t2_first_raw = _clean_person_name(_str_or_none("trustor_2_first_name"))
    t2_last_raw = _clean_person_name(_str_or_none("trustor_2_last_name"))
    t1_first_derived, t1_last_derived = _name_parts(t1_full)
    t2_first_derived, t2_last_derived = _name_parts(t2_full)
    t1_first = t1_first_raw or t1_first_derived
    t1_last = t1_last_raw or t1_last_derived
    t2_first = t2_first_raw or t2_first_derived
    t2_last = t2_last_raw or t2_last_derived

    property_address = _validate_property_address(_str_or_none("property_address"))

    return ExtractedFields(
        trustor_1_full_name=t1_full,
        trustor_1_first_name=t1_first,
        trustor_1_last_name=t1_last,
        trustor_2_full_name=t2_full,
        trustor_2_first_name=t2_first,
        trustor_2_last_name=t2_last,
        property_address=property_address,
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
