from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_URL = f"{BASE_URL}/web/search/DOCSEARCH1213S1"
SEARCH_POST_URL = f"{BASE_URL}/web/searchPost/DOCSEARCH1213S1"
SEARCH_RESULTS_URL = f"{BASE_URL}/web/searchResults/DOCSEARCH1213S1"
ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
DOCUMENTS_DIR = OUTPUT_DIR / "documents"
DEFAULT_DOCUMENT_TYPES = [
    "LIS PENDENS",
    "LIS PENDENS RELEASE",
    "TRUSTEES DEED UPON SALE",
    "SHERIFFS DEED",
    "NOTICE OF TRUSTEES SALE",
    "TREASURERS DEED",
    "AMENDED STATE LIEN",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
]
DOCUMENT_TYPE_ALIASES = {
    "TRUSTEES DEED": "TRUSTEES DEED UPON SALE",
    "NOTICE OF TRUSTEE SALE": "NOTICE OF TRUSTEES SALE",
}
DEFAULT_MODEL_CANDIDATES = [
    os.environ.get("COCONINO_GROQ_MODEL", "") or os.environ.get("GROQ_MODEL", "llama-3.3-70b"),
]


def _normalize_groq_model(model: str) -> str:
    m = (model or "").strip()
    if m == "llama-3.3-70b":
        return "llama-3.3-70b-versatile"
    return m or "llama-3.3-70b-versatile"

COCONINO_SYSTEM_PROMPT = """
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


def _normalize_principal_amount_numeric(value: str) -> str:
    s = str(value or "").strip()
    if not s or s.upper() == "NOT_FOUND":
        return ""
    s = re.sub(r"[,$\s]", "", s)
    if not s or not _NUMERIC_AMOUNT_RE.match(s):
        return ""
    try:
        val = float(s)
    except Exception:
        return ""
    if val < 1000:
        return ""
    return f"{val:.2f}".rstrip("0").rstrip(".")


def sanitize_property_address(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,;:-")
    if not text:
        return ""
    text = re.sub(
        r"^(?:property\s+address|situs\s+address|premises\s+address|commonly\s+known\s+as|located\s+at)\s*[:\-]\s*",
        "",
        text,
        flags=re.I,
    ).strip(" ,;:-")
    text = re.split(
        r"\b(?:APN|ASSESSOR(?:'S)?\s+PARCEL|REQUESTED\s+BY|WHEN\s+RECORDED|LEGAL\s+DESCRIPTION|RECORDING\s+FEE|RETURN\s+TO|MAIL\s+TO)\b",
        text,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,;:-")
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")
    words = [w for w in text.split(" ") if w]
    if len(words) > 8:
        return ""
    if len(text) > 180:
        return ""
    return text




@dataclass
class ExtractedRecord:
    document_id: str
    recording_number: str
    document_type: str
    recording_date: str
    grantors: list[str]
    grantees: list[str]
    legal_descriptions: list[str]
    property_address: str
    principal_amount: str
    detail_url: str
    source_file: str
    raw_html: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "recordingNumber": self.recording_number,
            "documentType": self.document_type,
            "recordingDate": self.recording_date,
            "grantors": self.grantors,
            "grantees": self.grantees,
            "legalDescriptions": self.legal_descriptions,
            "propertyAddress": self.property_address,
            "principalAmount": self.principal_amount,
            "detailUrl": self.detail_url,
            "sourceFile": self.source_file,
        }


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    candidates = [ROOT_DIR / ".env", ROOT_DIR.parent / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    for key, value in env.items():
        os.environ.setdefault(key, value)
    return env


def available_html_files() -> list[str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path.name for path in OUTPUT_DIR.glob("*.html"))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def default_last_three_month_range() -> tuple[str, str]:
    end = datetime.now()
    start = end - timedelta(days=90)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _normalize_date(value: str) -> str:
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def _normalize_document_types(document_types: list[str] | None) -> list[str]:
    if not document_types:
        return list(DEFAULT_DOCUMENT_TYPES)
    requested = [DOCUMENT_TYPE_ALIASES.get(item.strip().upper(), item.strip()) for item in document_types if item.strip()]
    invalid = [item for item in requested if item.upper() not in {value.upper() for value in DEFAULT_DOCUMENT_TYPES}]
    if invalid:
        raise ValueError(f"Unsupported document types: {', '.join(invalid)}")
    allowed_lookup = {value.upper(): value for value in DEFAULT_DOCUMENT_TYPES}
    return [allowed_lookup[item.upper()] for item in requested]


def _search_headers(cookie: str, ajax: bool = False, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": os.environ.get(
            "COCONINO_USER_AGENT",
            "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        ),
        "Referer": SEARCH_URL,
        "Connection": "keep-alive",
    }
    if ajax:
        headers.update({"Accept": "*/*", "X-Requested-With": "XMLHttpRequest", "ajaxrequest": "true"})
    else:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _parse_recording_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for pattern in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def latest_saved_results_html() -> Path:
    patterns = ["search_results_ajax_*.html", "live_search_results_page_*.html", "session_results_page_*.html"]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(OUTPUT_DIR.glob(pattern))
    candidates = sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("No saved Coconino search results HTML found")
    return candidates[0]


def _filter_records(records: list[dict[str, Any]], start_date: str, end_date: str, document_types: list[str]) -> list[dict[str, Any]]:
    start_dt = datetime.strptime(_normalize_date(start_date), "%Y-%m-%d")
    end_dt = datetime.strptime(_normalize_date(end_date), "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    requested_types = {item.upper() for item in _normalize_document_types(document_types)}
    filtered: list[dict[str, Any]] = []
    for record in records:
        record_dt = _parse_recording_datetime(str(record.get("recordingDate", "")))
        record_type = str(record.get("documentType", "")).upper()
        if record_dt is None:
            continue
        if not (start_dt <= record_dt <= end_dt):
            continue
        if requested_types and record_type not in requested_types:
            continue
        filtered.append(record)
    return filtered


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        key = (
            str(record.get("documentId", "")).strip(),
            str(record.get("recordingNumber", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _build_search_response(
    *,
    start_date: str,
    end_date: str,
    document_types: list[str],
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    html_files: list[str],
    csv_path: Path,
    data_source: str,
    live_error: str,
    include_document_analysis: bool,
    document_limit: int,
    use_groq: bool,
    used_fallback: bool,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "singleEndpoint": "/search",
        "recordCount": len(records),
        "records": records,
        "summary": summary,
        "htmlFiles": html_files,
        "csvFile": csv_path.name,
        "csvPath": str(csv_path),
        "dataSource": data_source,
        "liveError": live_error,
        "requestedGroq": use_groq,
        "includeDocumentAnalysis": include_document_analysis,
        "documentLimit": document_limit,
        "request": {
            "startDate": start_date,
            "endDate": end_date,
            "documentTypes": document_types,
            "includeDocumentAnalysis": include_document_analysis,
            "documentLimit": document_limit,
            "useGroq": use_groq,
        },
        "source": {
            "mode": data_source,
            "usedFallback": used_fallback,
            "liveError": live_error,
            "htmlFiles": html_files,
        },
        "outputs": {
            "csvFile": csv_path.name,
            "csvPath": str(csv_path),
        },
        "stats": {
            "recordCount": len(records),
            "page": summary.get("page"),
            "pageCount": summary.get("pageCount"),
            "totalResults": summary.get("totalResults"),
        },
        "warnings": warnings or [],
    }
    return payload


def _save_live_html(prefix: str, body: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{_timestamp()}.html"
    path = OUTPUT_DIR / filename
    path.write_text(body, encoding="utf-8")
    return filename


def run_live_search(
    start_date: str,
    end_date: str,
    document_types: list[str] | None = None,
    page_limit: int | None = None,
    cookie: str | None = None,
    save_html: bool = True,
) -> dict[str, Any]:
    load_env()
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        raise RuntimeError("Coconino session cookie is required via X-Coconino-Cookie header or COCONINO_COOKIE env var")
    opener = build_opener(HTTPCookieProcessor())
    opener.open(Request(SEARCH_URL, headers=_search_headers(effective_cookie)), timeout=30).read()
    normalized_types = _normalize_document_types(document_types)
    # Payload exactly mirrors the browser network request.
    # Each document type is posted as a separate "field_selfservice_documentTypes-searchInput"
    # entry (the JS autocomplete widget appends hidden inputs with that suffix).
    # All other scaffold fields are sent empty, as the server validates their presence.
    payload: list[tuple[str, str]] = [
        ("field_DocNum", ""),
        ("field_rdate_DOT_StartDate", _normalize_date(start_date)),
        ("field_rdate_DOT_EndDate", _normalize_date(end_date)),
        ("field_BothID-containsInput", "Contains Any"),
        ("field_BothID", ""),
        ("field_BookPageID_DOT_Book", ""),
        ("field_BookPageID_DOT_Page", ""),
        ("field_PlattedID_DOT_Subdivision-containsInput", "Contains Any"),
        ("field_PlattedID_DOT_Subdivision", ""),
        ("field_PlattedID_DOT_Lot", ""),
        ("field_PlattedID_DOT_Block", ""),
        ("field_PlattedID_DOT_Tract", ""),
        ("field_LegalCompID_DOT_QuarterSection-containsInput", "Contains Any"),
        ("field_LegalCompID_DOT_QuarterSection", ""),
        ("field_LegalCompID_DOT_Section", ""),
        ("field_LegalCompID_DOT_Township", ""),
        ("field_LegalCompID_DOT_Range", ""),
    ]
    for doc_type in normalized_types:
        payload.append(("field_selfservice_documentTypes-searchInput", doc_type))
    payload.append(("field_selfservice_documentTypes-containsInput", "Contains Any"))
    payload.append(("field_selfservice_documentTypes", ""))  # autocomplete text box — always empty
    post_request = Request(
        SEARCH_POST_URL,
        data=urlencode(payload).encode("utf-8"),
        headers=_search_headers(effective_cookie, content_type="application/x-www-form-urlencoded"),
        method="POST",
    )
    post_body = opener.open(post_request, timeout=60).read().decode("utf-8", errors="ignore")
    post_file = _save_live_html("live_search_post", post_body) if save_html else ""

    all_records: list[dict[str, Any]] = []
    html_files: list[str] = [post_file] if post_file else []
    summary: dict[str, Any] = {}
    current_page = 1
    while True:
        request = Request(
            f"{SEARCH_RESULTS_URL}?page={current_page}",
            headers=_search_headers(effective_cookie, ajax=True),
            method="GET",
        )
        body = opener.open(request, timeout=60).read().decode("utf-8", errors="ignore")
        source_name = _save_live_html(f"live_search_results_page_{current_page}", body) if save_html else f"live_page_{current_page}.html"
        if save_html:
            html_files.append(source_name)
        parsed = parse_search_results_html(body, source_file=source_name)
        page_summary = parsed.get("summary", {})
        summary = page_summary or summary
        records = parsed.get("records", [])
        if not records:
            break
        all_records.extend(records)
        total_pages = int(page_summary.get("pageCount") or current_page)
        if current_page >= total_pages:
            break
        if page_limit is not None and current_page >= page_limit:
            break
        current_page += 1

    summary = {
        **summary,
        "requestedStartDate": _normalize_date(start_date),
        "requestedEndDate": _normalize_date(end_date),
        "requestedDocumentTypes": normalized_types,
        "pagesFetched": current_page,
    }
    return {"summary": summary, "records": all_records, "htmlFiles": [name for name in html_files if name]}


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("_") or "document"


def build_document_pdf_url(document_id: str, recording_number: str, index: int = 1) -> str:
    """Legacy fallback URL (DEGRADED format). Use fetch_document_real_pdf_url() instead."""
    clean_document_id = document_id.strip()
    clean_recording_number = recording_number.strip()
    if not clean_document_id or not clean_recording_number:
        raise ValueError("document_id and recording_number are required")
    return (
        f"{BASE_URL}/web/document/servepdf/"
        f"DEGRADED-{clean_document_id}.{index}.pdf/{clean_recording_number}.pdf?index={index}"
    )


# UUID v4 regex used in Coconino's document-image URLs
_UUID_RE = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"


def _extract_pdf_guid_from_html(html_body: str) -> tuple[str, str] | None:
    """Scan a detail-page HTML for a pdfjs viewer link and return (guid, base_filename).

    The link looks like:
      /web/document-image-pdfjs/{doc_id}/{guid}/{filename}.pdf?allowDownload=true&index=N
    """
    pattern = re.compile(
        rf"/web/document-image-pdfjs/[^/]+/({_UUID_RE})/([^\"'/?]+?)(?:\.pdf|\.PDF)",
        flags=re.IGNORECASE,
    )
    match = pattern.search(html_body)
    if match:
        return match.group(1), match.group(2)
    return None


def _extract_iframe_pdf_path(html_body: str) -> str | None:
    """Scan a pdfjs-viewer HTML page for the embedded direct-PDF path.

    Looks for: src="/web/document-image-pdf/{doc_id}/{guid}/{filename}.pdf?index=N"
    """
    pattern = re.compile(
        r'(?:src|data)=["\'](/web/document-image-pdf/[^"\'?]+\.pdf[^"\']*)["\']',
        flags=re.IGNORECASE,
    )
    match = pattern.search(html_body)
    return match.group(1) if match else None


def fetch_document_real_pdf_url(
    document_id: str,
    cookie: str,
    index: int = 1,
    timeout_s: int = 60,
) -> str:
    """Discover the authenticated PDF download URL for a Coconino county document.

    Steps:
      1. Fetch the document detail page.
      2. Look for a pdfjs viewer link → extract GUID + base filename.
         Transform to: /web/document-image-pdf/{id}/{guid}/{file}-{idx}.pdf?index={idx}
      3. Fallback A: look for a direct /web/document-image-pdf/ link in the detail page.
      4. Fallback B: fetch the pdfjs viewer HTML and parse its iframe src.

    Returns the absolute PDF download URL ready for GET with the session cookie.
    """
    effective_cookie = cookie.strip()
    detail_url = f"{BASE_URL}/web/document/{document_id}?search=DOCSEARCH1213S1"
    req = Request(detail_url, headers=_document_headers(document_id, effective_cookie), method="GET")
    with urlopen(req, timeout=timeout_s) as resp:
        detail_html = resp.read().decode("utf-8", errors="ignore")

    # --- Primary: pdfjs link in detail page → direct URL via URL transformation ---
    guid_result = _extract_pdf_guid_from_html(detail_html)
    if guid_result:
        guid, base_filename = guid_result
        # pdfjs:  /web/document-image-pdfjs/{id}/{guid}/{file}.pdf?...&index=N
        # direct: /web/document-image-pdf/{id}/{guid}/{file}-N.pdf?index=N
        return (
            f"{BASE_URL}/web/document-image-pdf/{document_id}/{guid}"
            f"/{base_filename}-{index}.pdf?index={index}"
        )

    # --- Fallback A: direct image-pdf link already present in detail page ---
    direct_pat = re.compile(
        rf"/web/document-image-pdf/[^/]+/{_UUID_RE}/[^\"'?]+\.pdf",
        flags=re.IGNORECASE,
    )
    direct_match = direct_pat.search(detail_html)
    if direct_match:
        path = direct_match.group(0)
        sep = "&" if "?" in path else "?"
        if "index=" not in path:
            path = f"{path}{sep}index={index}"
        return f"{BASE_URL}{path}"

    # --- Fallback B: fetch the pdfjs viewer page and parse its iframe src ---
    pdfjs_link_match = re.search(
        r'href=["\'](?P<path>/web/document-image-pdfjs/[^"\']+)["\']',
        detail_html,
        flags=re.IGNORECASE,
    )
    if pdfjs_link_match:
        pdfjs_url = f"{BASE_URL}{pdfjs_link_match.group('path')}"
        pdfjs_req = Request(pdfjs_url, headers=_document_headers(document_id, effective_cookie), method="GET")
        with urlopen(pdfjs_req, timeout=timeout_s) as pdfjs_resp:
            pdfjs_html = pdfjs_resp.read().decode("utf-8", errors="ignore")
        iframe_path = _extract_iframe_pdf_path(pdfjs_html)
        if iframe_path:
            sep = "&" if "?" in iframe_path else "?"
            if "index=" not in iframe_path:
                iframe_path = f"{iframe_path}{sep}index={index}"
            return f"{BASE_URL}{iframe_path}"

    raise RuntimeError(
        f"Could not discover PDF URL for document {document_id}. "
        f"Detail page returned {len(detail_html)} chars. "
        f"The document may not have an associated image PDF."
    )


def _document_headers(document_id: str, cookie: str) -> dict[str, str]:
    headers = {
        "User-Agent": os.environ.get(
            "COCONINO_USER_AGENT",
            "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        ),
        "Accept": "text/html, */*; q=0.01",
        "Referer": f"{BASE_URL}/web/document/{document_id}?search=DOCSEARCH1213S1",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "keep-alive",
    }
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    return headers


def _extract_address_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    pattern = re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.#'/-]+(?:\s+[A-Za-z0-9.#'/-]+){1,6}\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|CIR|CIRCLE|WAY)\b(?:[^\n,]{0,40})",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text or ""):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" ,")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _extract_currency_values(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text or "", flags=re.IGNORECASE):
        value = re.sub(r"\s+", "", match.group(0)).strip()
        if value and value not in values:
            values.append(value)
    return values


def _extract_principal_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    pattern = re.compile(
        r"(?:principal(?:\s+amount)?|loan\s+amount|original\s+amount|indebtedness|note\s+amount)[^$]{0,80}(\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text or ""):
        amount = re.sub(r"\s+", "", match.group(1)).strip()
        if amount and amount not in candidates:
            candidates.append(amount)
    return candidates


def fetch_document_detail_fields(document_id: str, cookie: str | None = None, timeout_s: int = 60) -> dict[str, Any]:
    load_env()
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        raise RuntimeError("Coconino session cookie is required for detail fetch")
    url = f"{BASE_URL}/web/document/{document_id}?search=DOCSEARCH1213S1"
    request = Request(url, headers=_document_headers(document_id, effective_cookie), method="GET")
    with urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="ignore")
    pairs = re.findall(r"<strong\s*>\s*([^<:]+):\s*</strong>\s*</div>\s*<div[^>]*>([\s\S]*?)</div>", body, flags=re.IGNORECASE)
    values: dict[str, list[str]] = {}
    for label, raw_value in pairs:
        clean_label = _clean_text(label).lower()
        texts = re.findall(r"<li[^>]*>([\s\S]*?)</li>", raw_value, flags=re.IGNORECASE)
        if texts:
            clean_values = [
                _clean_text(item)
                for item in texts
                if _clean_text(item) and _clean_text(item).lower() != "show more..."
            ]
        else:
            clean_values = [_clean_text(raw_value)] if _clean_text(raw_value) else []
        if clean_values:
            values[clean_label] = clean_values
    property_address = ""
    for key in ("property address", "site address", "address", "situs address"):
        if values.get(key):
            property_address = values[key][0]
            break
    if not property_address:
        address_candidates = _extract_address_candidates(_clean_text(body))
        if address_candidates:
            property_address = address_candidates[0]

    principal_amount = ""
    amount_keys = (
        "principal amount",
        "principal",
        "loan amount",
        "original amount",
        "original principal",
        "amount",
        "deed of trust amount",
        "unpaid principal",
    )
    for key in amount_keys:
        if values.get(key):
            joined = " ".join(values.get(key, []))
            money = _extract_currency_values(joined)
            if money:
                principal_amount = money[0]
                break
    if not principal_amount:
        principal_candidates = _extract_principal_candidates(_clean_text(body))
        if principal_candidates:
            principal_amount = principal_candidates[0]

    subdivision = ""
    lot = ""
    platted_match = re.search(r"Subdivision:\s*</strong>\s*([^<]+).*?Unit/Lot:\s*</strong>\s*([^<]+)", body, flags=re.IGNORECASE | re.DOTALL)
    if platted_match:
        subdivision = _clean_text(platted_match.group(1))
        lot = _clean_text(platted_match.group(2))
    if not property_address and subdivision:
        property_address = f"Subdivision {subdivision}"
        if lot:
            property_address = f"{property_address} Lot {lot}"

    property_address = sanitize_property_address(property_address)
    principal_amount = _normalize_principal_amount_numeric(principal_amount)
    return {
        "detailUrl": url,
        "propertyAddress": property_address,
        "principalAmount": principal_amount,
        "grantors": values.get("grantor", []),
        "grantees": values.get("grantee", []),
        "subdivision": subdivision,
        "lot": lot,
        "detailHtmlLength": len(body),
    }


def fetch_document_pdf(
    document_id: str,
    recording_number: str,
    index: int = 1,
    cookie: str | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Download a Coconino county document PDF.

    Discovers the real PDF URL by inspecting the document detail page for the
    GUID-based /web/document-image-pdf/ link, rather than the old broken
    DEGRADED-* servepdf format which returned empty files.
    """
    load_env()
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if not effective_cookie:
        raise RuntimeError(
            "Coconino session cookie is required via X-Coconino-Cookie header or COCONINO_COOKIE env var"
        )
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Discover the real PDF URL via the document detail page (GUID-based URL).
    pdf_url = fetch_document_real_pdf_url(
        document_id=document_id,
        cookie=effective_cookie,
        index=index,
        timeout_s=timeout_s,
    )

    request = Request(pdf_url, headers=_document_headers(document_id, effective_cookie), method="GET")
    with urlopen(request, timeout=timeout_s) as response:
        body = response.read()
        content_type = response.headers.get("content-type", "")

    if b"<html" in body[:200].lower() or "text/html" in content_type.lower():
        preview = body.decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"Expected PDF but received HTML response: {preview}")

    if len(body) == 0:
        raise RuntimeError(f"Server returned an empty PDF from {pdf_url}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{_safe_slug(document_id)}_{_safe_slug(recording_number)}_{index}_{timestamp}.pdf"
    pdf_path = DOCUMENTS_DIR / filename
    pdf_path.write_bytes(body)
    return {
        "documentUrl": pdf_url,
        "pdfPath": str(pdf_path),
        "pdfSize": len(body),
        "contentType": content_type,
    }


def _run_command(command: list[str], timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)


def extract_text_from_pdf(pdf_path: str, timeout_s: int = 60) -> str:
    pdftotext_path = shutil.which("pdftotext")
    if not pdftotext_path:
        return ""
    result = _run_command([pdftotext_path, pdf_path, "-"], timeout_s=timeout_s)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def ocr_pdf(pdf_path: str, timeout_s: int = 240) -> dict[str, Any]:
    pdftoppm_path = shutil.which("pdftoppm")
    tesseract_path = shutil.which("tesseract")
    if not pdftoppm_path or not tesseract_path:
        raise RuntimeError("pdftoppm and tesseract are required for OCR")
    pdfinfo_path = shutil.which("pdfinfo")
    page_count = None
    if pdfinfo_path:
        info_result = _run_command([pdfinfo_path, pdf_path], timeout_s=30)
        if info_result.returncode == 0:
            match = re.search(r"^Pages:\s+(\d+)", info_result.stdout, flags=re.MULTILINE)
            if match:
                page_count = int(match.group(1))
    working_dir = DOCUMENTS_DIR / f"ocr_{_safe_slug(Path(pdf_path).stem)}"
    if working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)
    prefix = working_dir / "page"
    render = _run_command([pdftoppm_path, "-png", pdf_path, str(prefix)], timeout_s=timeout_s)
    if render.returncode != 0:
        raise RuntimeError(render.stderr.strip() or "pdftoppm failed")
    images = sorted(working_dir.glob("page-*.png"))
    if not images:
        raise RuntimeError("No images were rendered from PDF")
    pages: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for image_path in images:
        ocr = _run_command([tesseract_path, str(image_path), "stdout"], timeout_s=timeout_s)
        if ocr.returncode != 0:
            raise RuntimeError(ocr.stderr.strip() or f"tesseract failed for {image_path.name}")
        text = ocr.stdout.strip()
        pages.append({"imagePath": str(image_path), "textLength": len(text)})
        if text:
            text_parts.append(text)
    full_text = "\n\n".join(text_parts).strip()
    text_path = working_dir / "ocr_text.txt"
    text_path.write_text(full_text, encoding="utf-8")
    return {
        "ocrText": full_text,
        "ocrTextPath": str(text_path),
        "pageCount": page_count or len(images),
        "pages": pages,
        "ocrMethod": "tesseract",
    }


def analyze_document_text_with_groq(
    document_id: str,
    recording_number: str,
    document_type: str,
    ocr_text: str,
    timeout_s: int = 90,
) -> dict[str, Any]:
    load_env()
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    user_payload = {
        "documentId": document_id,
        "recordingNumber": recording_number,
        "documentType": document_type,
        "ocrText": ocr_text[:18000],
    }
    messages = [
        {"role": "system", "content": COCONINO_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    model = _normalize_groq_model(str(DEFAULT_MODEL_CANDIDATES[0] if DEFAULT_MODEL_CANDIDATES else ""))
    try:
        content = _groq_request(messages, api_key=api_key, model=model, timeout_s=timeout_s)
        data = _parse_llm_json(content)
        if isinstance(data, dict):
            data["model"] = model
            return data
    except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Groq document analysis failed (model={model}): {exc}")

    raise RuntimeError(f"Groq document analysis failed (model={model}): empty/invalid response")


def fetch_document_ocr_and_analysis(
    document_id: str,
    recording_number: str,
    index: int = 1,
    document_type: str = "",
    cookie: str | None = None,
    use_groq: bool = True,
) -> dict[str, Any]:
    download = fetch_document_pdf(
        document_id=document_id,
        recording_number=recording_number,
        index=index,
        cookie=cookie,
    )
    direct_text = extract_text_from_pdf(download["pdfPath"])
    ocr_result = {
        "ocrText": direct_text,
        "ocrTextPath": "",
        "pageCount": 0,
        "pages": [],
        "ocrMethod": "pdftotext",
    }
    if len(direct_text.strip()) < 80:
        ocr_result = ocr_pdf(download["pdfPath"])
    groq_analysis: dict[str, Any] = {}
    groq_error = ""
    used_groq = False
    
    # Force use_groq if ocrText is available (User Requirement: No regex fallback dependence)
    if use_groq and ocr_result["ocrText"].strip():
        try:
            groq_analysis = analyze_document_text_with_groq(
                document_id=document_id,
                recording_number=recording_number,
                document_type=document_type,
                ocr_text=ocr_result["ocrText"],
            )
            used_groq = True
        except Exception as exc:
            groq_error = str(exc)
            
    preview_text = ocr_result["ocrText"][:1500]
    return {
        "documentId": document_id,
        "recordingNumber": recording_number,
        "documentType": document_type,
        **download,
        "ocrMethod": ocr_result["ocrMethod"],
        "ocrTextPath": ocr_result["ocrTextPath"],
        "ocrTextLength": len(ocr_result["ocrText"]),
        "ocrTextPreview": preview_text,
        "pageCount": ocr_result["pageCount"],
        "ocrPages": ocr_result["pages"],
        "requestedGroq": use_groq,
        "usedGroq": used_groq,
        "groqError": groq_error,
        "groqAnalysis": groq_analysis,
        # Regex candidates are removed/empty to ensure we don't rely on them
        "addressCandidates": [], 
        "principalCandidates": [],
    }


def resolve_html_file(html_file: str) -> Path:
    candidate = (OUTPUT_DIR / html_file).resolve() if not os.path.isabs(html_file) else Path(html_file).resolve()
    allowed_root = ROOT_DIR.resolve()
    if allowed_root not in candidate.parents and candidate != allowed_root:
        raise ValueError("html_file must stay inside conino directory")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"HTML file not found: {html_file}")
    return candidate


def _clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_label(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", label or "").strip().lower()


def _parse_summary(html_text: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    match = re.search(
        r"Showing\s+page\s+(\d+)\s+of\s+(\d+)\s+for\s+(\d+)\s+Total Results",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        summary["page"] = int(match.group(1))
        summary["pageCount"] = int(match.group(2))
        summary["totalResults"] = int(match.group(3))
    filter_match = re.search(
        r"<div class=\"selfServiceSearchResultHeaderLeft\">\s*Recordings\s+(.*?)</div>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if filter_match:
        summary["filterSummary"] = _clean_text(filter_match.group(1))
    return summary


def _row_blocks(html_text: str) -> list[str]:
    pattern = re.compile(
        r"(<li[^>]*class=\"[^\"]*ss-search-row[^\"]*\"[\s\S]*?<p class=\"selfServiceSearchFullResult selfServiceSearchResultNavigation\">[\s\S]*?</div>\s*</li>)",
        flags=re.IGNORECASE,
    )
    rows = [match.group(1) for match in pattern.finditer(html_text)]
    if rows:
        return rows
    fallback_pattern = re.compile(
        r"(<li[^>]*class=\"[^\"]*ss-search-row[^\"]*\"[\s\S]*?</li>)",
        flags=re.IGNORECASE,
    )
    return [match.group(1) for match in fallback_pattern.finditer(html_text)]


def _extract_column_values(block: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    ul_pattern = re.compile(
        r"<ul class=\"selfServiceSearchResultColumn[^\"]*\">([\s\S]*?)</ul>",
        flags=re.IGNORECASE,
    )
    li_pattern = re.compile(r"<li[^>]*>([\s\S]*?)</li>", flags=re.IGNORECASE)
    bold_pattern = re.compile(r"<b>([\s\S]*?)</b>", flags=re.IGNORECASE)
    for ul_match in ul_pattern.finditer(block):
        ul_body = ul_match.group(1)
        li_matches = li_pattern.findall(ul_body)
        if not li_matches:
            continue
        label = _normalize_label(_clean_text(li_matches[0]))
        values = [_clean_text(value) for value in bold_pattern.findall(ul_body)]
        if label:
            existing = result.setdefault(label, [])
            existing.extend(value for value in values if value and value not in existing)
    return result


def parse_search_results_html(html_text: str, source_file: str) -> dict[str, Any]:
    rows: list[ExtractedRecord] = []
    for block in _row_blocks(html_text):
        document_id_match = re.search(r'data-documentid="([^"]+)"', block, flags=re.IGNORECASE)
        href_match = re.search(r'data-href="([^"]+)"', block, flags=re.IGNORECASE)
        header_match = re.search(r"<h1>([\s\S]*?)</h1>", block, flags=re.IGNORECASE)
        header_text = _clean_text(header_match.group(1) if header_match else "")
        header_parts = [part.strip() for part in re.split(r"\s*·\s*", header_text) if part.strip()]
        columns = _extract_column_values(block)
        document_id = document_id_match.group(1) if document_id_match else ""
        if len(header_parts) < 3 and header_text:
            header_parts = [part.strip() for part in re.split(r"\s*[•·]\s*", header_text) if part.strip()]
        recording_number = header_parts[0] if header_parts else header_text
        document_type = header_parts[1] if len(header_parts) > 1 else ""
        recording_date = header_parts[2] if len(header_parts) > 2 else ""
        detail_path = href_match.group(1) if href_match else ""
        rows.append(
            ExtractedRecord(
                document_id=document_id,
                recording_number=recording_number,
                document_type=document_type,
                recording_date=recording_date,
                grantors=columns.get("grantor", []),
                grantees=columns.get("grantee", []),
                legal_descriptions=columns.get("legal", []),
                property_address=(columns.get("legal", [""])[0] if columns.get("legal") else ""),
                principal_amount="",
                detail_url=f"{BASE_URL}{detail_path}" if detail_path.startswith("/") else detail_path,
                source_file=source_file,
                raw_html=block,
            )
        )
    return {
        "summary": _parse_summary(html_text),
        "records": [row.as_dict() for row in rows],
        "rawRecords": rows,
    }


def _chunk_records(records: list[ExtractedRecord], batch_size: int) -> list[list[ExtractedRecord]]:
    return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]


def _groq_request(messages: list[dict[str, str]], api_key: str, model: str, timeout_s: int) -> str:
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    request = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
        data = json.loads(body)
        return data["choices"][0]["message"]["content"]
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        if exc.code in (401, 403):
            hint = (
                "Groq access denied (HTTP %s). "
                "Check GROQ_API_KEY validity and network/egress policy (VPN, proxy, firewall, datacenter IP restrictions)."
            ) % exc.code
            if body:
                hint = f"{hint} response={body[:220]}"
            raise RuntimeError(hint)
        raise


def _parse_llm_json(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned) if cleaned else {}
    if not isinstance(data, dict):
        raise RuntimeError("Groq returned non-object JSON")
    return data


def enrich_with_groq(records: list[ExtractedRecord], batch_size: int = 5, timeout_s: int = 60) -> list[dict[str, Any]]:
    load_env()
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")
    normalized: list[dict[str, Any]] = []

    for batch in _chunk_records(records, max(1, batch_size)):
        user_payload = {
            "rows": [
                {
                    "preparsed": row.as_dict(),
                    "htmlSnippet": row.raw_html[:6000],
                }
                for row in batch
            ]
        }
        messages = [
            {"role": "system", "content": COCONINO_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        last_error: Exception | None = None
        content = ""
        for model in DEFAULT_MODEL_CANDIDATES:
            try:
                content = _groq_request(messages, api_key=api_key, model=model, timeout_s=timeout_s)
                last_error = None
                break
            except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
        if last_error is not None and not content:
            raise RuntimeError(f"Groq extraction failed: {last_error}")
        parsed = _parse_llm_json(content)
        rows = parsed.get("rows") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Groq returned unexpected JSON shape")
        for index, item in enumerate(rows):
            base = batch[index].as_dict()
            normalized.append(
                {
                    "documentId": str(item.get("documentId") or base["documentId"]),
                    "recordingNumber": str(item.get("recordingNumber") or base["recordingNumber"]),
                    "documentType": str(item.get("documentType") or base["documentType"]),
                    "recordingDate": str(item.get("recordingDate") or base["recordingDate"]),
                    # Prefer LLM extracted grantors/grantees, fall back to base
                    "grantors": _string_list(item.get("grantors"), base["grantors"]),
                    "grantees": _string_list(item.get("grantees"), base["grantees"]),
                    "legalDescriptions": _string_list(item.get("legalDescriptions"), base["legalDescriptions"]),
                    "detailUrl": str(item.get("detailUrl") or base["detailUrl"]),
                    "sourceFile": base["sourceFile"],
                    # Capture additional LLM fields
                    "trustor": str(item.get("trustor") or ""),
                    "trustee": str(item.get("trustee") or ""),
                    "beneficiary": str(item.get("beneficiary") or ""),
                    "principalAmount": str(item.get("principalAmount") or ""),
                    "propertyAddress": str(item.get("propertyAddress") or ""),
                }
            )
    return normalized


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if cleaned:
            return cleaned
    return fallback


def export_csv(records: list[dict[str, Any]], csv_name: str | None = None) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    filename = csv_name.strip() if csv_name else f"coconino_results_{timestamp}.csv"
    if not filename.lower().endswith(".csv"):
        filename = f"{filename}.csv"
    path = (OUTPUT_DIR / filename).resolve()
    if OUTPUT_DIR.resolve() not in path.parents and path != OUTPUT_DIR.resolve():
        raise ValueError("csv_name must stay inside conino/output")
    fieldnames = [
        "documentId",
        "recordingNumber",
        "documentType",
        "recordingDate",
        "grantors",
        "grantees",
        "legalDescriptions",
        "propertyAddress",
        "principalAmount",
        "detailUrl",
        "sourceFile",
        "documentUrl",
        "ocrMethod",
        "ocrTextPreview",
        "ocrTextPath",
        "usedGroq",
        "groqError",
        "documentAnalysisError",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            document_analysis = record.get("documentAnalysis") or {}
            writer.writerow(
                {
                    **record,
                    "grantors": " | ".join(record.get("grantors", [])),
                    "grantees": " | ".join(record.get("grantees", [])),
                    "legalDescriptions": " | ".join(record.get("legalDescriptions", [])),
                    "propertyAddress": record.get("propertyAddress", ""),
                    "principalAmount": record.get("principalAmount", ""),
                    "documentUrl": document_analysis.get("documentUrl", ""),
                    "ocrMethod": document_analysis.get("ocrMethod", ""),
                    "ocrTextPreview": (document_analysis.get("ocrTextPreview", "") or "")[:500],
                    "ocrTextPath": document_analysis.get("ocrTextPath", ""),
                    "usedGroq": document_analysis.get("usedGroq", record.get("usedGroq", False)),
                    "groqError": document_analysis.get("groqError", record.get("groqError", "")),
                    "documentAnalysisError": record.get("documentAnalysisError", ""),
                }
            )
    return path


def fetch_session_results_pages(cookie: str, page_limit: int | None = None, save_html: bool = True) -> dict[str, Any]:
    opener = build_opener(HTTPCookieProcessor())
    all_records: list[dict[str, Any]] = []
    html_files: list[str] = []
    summary: dict[str, Any] = {}
    current_page = 1
    while True:
        request = Request(f"{SEARCH_RESULTS_URL}?page={current_page}", headers=_search_headers(cookie, ajax=True), method="GET")
        body = opener.open(request, timeout=60).read().decode("utf-8", errors="ignore")
        source_name = _save_live_html(f"session_results_page_{current_page}", body) if save_html else f"session_page_{current_page}.html"
        if save_html:
            html_files.append(source_name)
        parsed = parse_search_results_html(body, source_file=source_name)
        page_summary = parsed.get("summary", {})
        summary = page_summary or summary
        records = parsed.get("records", [])
        if not records:
            break
        all_records.extend(records)
        total_pages = int(page_summary.get("pageCount") or current_page)
        if current_page >= total_pages:
            break
        if page_limit is not None and current_page >= page_limit:
            break
        current_page += 1
    return {"summary": summary, "records": all_records, "htmlFiles": html_files}


def enrich_records_with_detail_fields(records: list[dict[str, Any]], cookie: str | None = None, max_records: int | None = None) -> list[dict[str, Any]]:
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    enriched: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        updated = dict(record)
        if effective_cookie and (max_records is None or index < max_records):
            try:
                detail = fetch_document_detail_fields(str(record.get("documentId", "")), cookie=effective_cookie)
                if detail.get("grantors"):
                    updated["grantors"] = detail["grantors"]
                if detail.get("grantees"):
                    updated["grantees"] = detail["grantees"]
                if detail.get("propertyAddress"):
                    updated["propertyAddress"] = sanitize_property_address(detail["propertyAddress"])
                if detail.get("principalAmount"):
                    updated["principalAmount"] = _normalize_principal_amount_numeric(detail["principalAmount"])
                if not updated.get("legalDescriptions") and detail.get("subdivision"):
                    legal = detail["subdivision"]
                    if detail.get("lot"):
                        legal = f"Subdivision {legal} Lot {detail['lot']}"
                    updated["legalDescriptions"] = [legal]
            except Exception as exc:
                updated["detailError"] = str(exc)
        if updated.get("propertyAddress"):
            updated["propertyAddress"] = sanitize_property_address(updated.get("propertyAddress", ""))
        if updated.get("principalAmount"):
            updated["principalAmount"] = _normalize_principal_amount_numeric(updated.get("principalAmount", ""))

        if not updated.get("propertyAddress") and updated.get("legalDescriptions"):
            updated["propertyAddress"] = sanitize_property_address(updated["legalDescriptions"][0])

        if not updated.get("propertyAddress"):
            updated["propertyAddress"] = "NOT_FOUND"
        if not updated.get("principalAmount"):
            updated["principalAmount"] = "NOT_FOUND"
        enriched.append(updated)
    return enriched


def search_to_csv(
    start_date: str | None = None,
    end_date: str | None = None,
    document_types: list[str] | None = None,
    use_groq: bool = True,
    csv_name: str | None = None,
    include_document_analysis: bool = False,
    document_limit: int = 0,
    document_index: int = 1,
    page_limit: int | None = None,
    cookie: str | None = None,
    save_html: bool = True,
    use_current_session_results: bool = False,
) -> dict[str, Any]:
    load_env()
    default_start, default_end = default_last_three_month_range()
    effective_start = start_date or default_start
    effective_end = end_date or default_end
    normalized_document_types = _normalize_document_types(document_types)
    live_error = ""
    data_source = "live-search"
    warnings: list[str] = []
    effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
    if use_current_session_results:
        session_results = fetch_session_results_pages(effective_cookie, page_limit=page_limit, save_html=save_html)
        records = _dedupe_records(list(session_results.get("records", [])))
        records = enrich_records_with_detail_fields(records, cookie=cookie, max_records=None)
        csv_path = export_csv(records, csv_name=csv_name)
        return _build_search_response(
            start_date=_normalize_date(effective_start),
            end_date=_normalize_date(effective_end),
            document_types=normalized_document_types,
            records=records,
            summary={**session_results.get("summary", {}), "mode": "current-session-results"},
            html_files=session_results.get("htmlFiles", []),
            csv_path=csv_path,
            data_source="current-session-results",
            live_error="",
            include_document_analysis=include_document_analysis,
            document_limit=document_limit,
            use_groq=use_groq,
            used_fallback=False,
        )
    try:
        live = run_live_search(
            start_date=effective_start,
            end_date=effective_end,
            document_types=document_types,
            page_limit=page_limit,
            cookie=cookie,
            save_html=save_html,
        )
        records = _dedupe_records(list(live.get("records", [])))
        summary = live.get("summary", {})
        html_files = live.get("htmlFiles", [])
    except Exception as exc:
        live_error = str(exc)
        data_source = "saved-html-fallback"
        warnings.append(f"Live county search failed; using fallback data. {live_error}")
        try:
            if not effective_cookie:
                raise RuntimeError("No session cookie available for pagination fallback")
            session_results = fetch_session_results_pages(effective_cookie, page_limit=page_limit, save_html=save_html)
            session_records = _dedupe_records(
                _filter_records(session_results.get("records", []), effective_start, effective_end, normalized_document_types)
            )
            if not session_records:
                raise RuntimeError("Session pagination fallback returned no matching records")
            records = session_records
            summary = {
                **session_results.get("summary", {}),
                "requestedStartDate": _normalize_date(effective_start),
                "requestedEndDate": _normalize_date(effective_end),
                "requestedDocumentTypes": normalized_document_types,
            }
            html_files = session_results.get("htmlFiles", [])
            data_source = "session-pagination-fallback"
        except Exception:
            try:
                fallback_path = latest_saved_results_html()
                parsed = parse_search_results_html(fallback_path.read_text(encoding="utf-8", errors="ignore"), fallback_path.name)
                records = _dedupe_records(_filter_records(parsed.get("records", []), effective_start, effective_end, normalized_document_types))
                summary = {
                    **parsed.get("summary", {}),
                    "requestedStartDate": _normalize_date(effective_start),
                    "requestedEndDate": _normalize_date(effective_end),
                    "requestedDocumentTypes": normalized_document_types,
                }
                html_files = [fallback_path.name]
                data_source = "saved-html-fallback"
            except FileNotFoundError:
                records = []
                summary = {
                    "requestedStartDate": _normalize_date(effective_start),
                    "requestedEndDate": _normalize_date(effective_end),
                    "requestedDocumentTypes": normalized_document_types,
                }
                html_files = []
                data_source = "no-fallback-data"
                warnings.append("No saved search results HTML is available in conino/output.")
    if include_document_analysis and records:
        effective_cookie = (cookie or os.environ.get("COCONINO_COOKIE", "")).strip()
        for index, record in enumerate(records):
            if index >= max(0, document_limit):
                break
            try:
                record["documentAnalysis"] = fetch_document_ocr_and_analysis(
                    document_id=str(record.get("documentId", "")),
                    recording_number=str(record.get("recordingNumber", "")),
                    index=document_index,
                    document_type=str(record.get("documentType", "")),
                    cookie=effective_cookie,
                    use_groq=use_groq,
                )
                if not record.get("propertyAddress"):
                    candidates = record["documentAnalysis"].get("addressCandidates") or []
                    if candidates:
                        record["propertyAddress"] = sanitize_property_address(candidates[0]) or "NOT_FOUND"
                if not record.get("principalAmount"):
                    amount_candidates = record["documentAnalysis"].get("principalCandidates") or []
                    if amount_candidates:
                        record["principalAmount"] = _normalize_principal_amount_numeric(amount_candidates[0]) or "NOT_FOUND"
            except Exception as exc:
                record["documentAnalysisError"] = str(exc)
    records = enrich_records_with_detail_fields(records, cookie=cookie, max_records=None)
    records = _dedupe_records(records)
    csv_path = export_csv(records, csv_name=csv_name)
    return _build_search_response(
        start_date=_normalize_date(effective_start),
        end_date=_normalize_date(effective_end),
        document_types=normalized_document_types,
        records=records,
        summary=summary,
        html_files=html_files,
        csv_path=csv_path,
        data_source=data_source,
        live_error=live_error,
        include_document_analysis=include_document_analysis,
        document_limit=document_limit,
        use_groq=use_groq,
        used_fallback=data_source != "live-search",
        warnings=warnings,
    )


def extract_to_csv(
    html_file: str,
    limit: int | None = None,
    offset: int = 0,
    use_groq: bool = True,
    csv_name: str | None = None,
    document_types: list[str] | None = None,
    cookie: str | None = None,
    enrich_details: bool = True,
) -> dict[str, Any]:
    load_env()
    path = resolve_html_file(html_file)
    html_text = path.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_search_results_html(html_text, source_file=path.name)
    raw_records: list[ExtractedRecord] = parsed.pop("rawRecords")
    if document_types:
        requested = {item.strip().lower() for item in document_types if item.strip()}
        raw_records = [row for row in raw_records if row.document_type.strip().lower() in requested]
    if offset > 0:
        raw_records = raw_records[offset:]
    if limit is not None and limit >= 0:
        raw_records = raw_records[:limit]
    groq_error = ""
    groq_used = False
    records = [row.as_dict() for row in raw_records]
    if use_groq and raw_records:
        try:
            records = enrich_with_groq(raw_records)
            groq_used = True
        except Exception as exc:
            groq_error = str(exc)
    if enrich_details:
        records = enrich_records_with_detail_fields(records, cookie=cookie, max_records=None)
    csv_path = export_csv(records, csv_name=csv_name)
    return {
        "ok": True,
        "summary": parsed.get("summary", {}),
        "htmlFile": path.name,
        "recordCount": len(records),
        "csvFile": csv_path.name,
        "csvPath": str(csv_path),
        "requestedGroq": use_groq,
        "usedGroq": groq_used,
        "groqError": groq_error,
        "enrichedDetails": enrich_details,
        "records": records,
    }
