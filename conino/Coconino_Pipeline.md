# Coconino County Real Estate Lead Scraping — Production Guide

> **Platform:** Tyler Technologies EagleWeb  
> **County:** Coconino County, Arizona  
> **Base URL:** `https://eagleassessor.coconino.az.gov:8444`  
> **Search ID:** `DOCSEARCH1213S1`  
> **Python:** 3.10+ (pyenv 3.10.13 confirmed)  
> **Status:** Production-ready, tested live (Mar 2026)

---

## Table of Contents

1. [What We Are Building](#1-what-we-are-building)
2. [Architecture Overview](#2-architecture-overview)
3. [Platform Deep-Dive — Tyler Technologies EagleWeb](#3-platform-deep-dive)
4. [Pipeline Stages — Detailed](#4-pipeline-stages-detailed)
5. [PDF URL Discovery — The Critical Fix](#5-pdf-url-discovery-the-critical-fix)
6. [OCR + LLM Extraction](#6-ocr--llm-extraction)
7. [Data Schema](#7-data-schema)
8. [Codebase Map](#8-codebase-map)
9. [Environment Setup](#9-environment-setup)
10. [Configuration Reference](#10-configuration-reference)
11. [Running the Pipeline](#11-running-the-pipeline)
12. [Cron Automation](#12-cron-automation)
13. [Troubleshooting](#13-troubleshooting)
14. [AI Prompt Template](#14-ai-prompt-template)

---

## 1. What We Are Building

An automated daily scraper that:

1. Logs into the Coconino County Recorder's public document search portal.
2. Searches for **distressed-property document types** filed in the last 30 days.
3. For each document: downloads the PDF, runs OCR, extracts the **property address** and **principal/loan amount**.
4. Outputs a clean CSV with every field needed to work a real estate lead.
5. Runs unattended on a cron schedule and never re-processes the same document twice.

### Target Document Types (distress indicators)

| Type | What it means |
|------|--------------|
| `LIS PENDENS` | Lawsuit filed against a property — pre-foreclosure signal |
| `LIS PENDENS RELEASE` | Lawsuit resolved — property may be listed again |
| `TRUSTEES DEED UPON SALE` | Property sold at trustee (foreclosure) auction |
| `SHERIFFS DEED` | Property sold at sheriff's sale |
| `NOTICE OF TRUSTEES SALE` | Upcoming foreclosure auction — advance notice |
| `TREASURERS DEED` | County sold property for unpaid taxes |
| `AMENDED STATE LIEN` | State lien amended |
| `STATE LIEN` | State government lien on property |
| `STATE TAX LIEN` | Unpaid state taxes — distress signal |
| `RELEASE STATE TAX LIEN` | State tax lien released |

### Final Output Fields (per document)

| Field | Source |
|-------|--------|
| `documentId` | Search results HTML |
| `recordingNumber` (Fee #) | Search results HTML |
| `documentType` | Search results HTML |
| `recordingDate` | Search results HTML |
| `grantors` | Search results + detail page |
| `grantees` | Search results + detail page |
| `propertyAddress` | Detail page → OCR → Groq |
| `principalAmount` | Detail page → OCR → Groq |
| `detailUrl` | Constructed from doc ID |
| `documentUrl` | Discovered from detail page GUID |
| `ocrTextPreview` | First 500 chars of OCR text |
| `usedGroq` | Boolean |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                       live_pipeline.py                       │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐ │
│  │ STAGE 1  │   │ STAGE 2  │   │ STAGE 3  │   │STAGE 4  │ │
│  │AUTH+FORM │──▶│ PAGINATE │──▶│  FILTER  │──▶│ DISPLAY │ │
│  │Playwright│   │requests  │   │client-   │   │real-time│ │
│  │          │   │(urllib)  │   │side only │   │table    │ │
│  └──────────┘   └──────────┘   └──────────┘   └─────────┘ │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌─────────┐ │
│  │ STAGE 5  │   │ STAGE 6  │   │ STAGE 7  │   │STAGE 8  │ │
│  │  DETAIL  │──▶│   OCR    │──▶│  GROQ    │──▶│  SAVE   │ │
│  │ address  │   │pdftotext │   │Llama-3.3 │   │  CSV    │ │
│  │principal │   │+tesseract│   │LLM parse │   │  JSON   │ │
│  └──────────┘   └──────────┘   └──────────┘   └─────────┘ │
└─────────────────────────────────────────────────────────────┘
              │
              ▼
        extractor.py  (all HTTP + parsing logic)
```

### Key Technology Choices

| Concern | Choice | Why |
|---------|--------|-----|
| Browser auth | **Playwright (Chromium)** | Handles CSRF tokens, JS-rendered forms, Cloudflare cookies, disclaimer clicks automatically |
| Pagination | **urllib (stdlib)** | Reuses the JSESSIONID already bound to the active search; no overhead |
| PDF extraction | **pdftotext** (poppler) → **tesseract** fallback | Fast text-based PDFs first; image-only scans handled by OCR |
| Address/amount parsing | **Groq Llama-3.3** → **regex fallback** | LLM handles noisy OCR text better than regex alone |
| Session persistence | **Playwright `storage_state.json`** | Survives restarts without re-authentication |
| Output | **CSV + JSON** | CSV for spreadsheet/CRM import; JSON for programmatic use |

---

## 3. Platform Deep-Dive

### Tyler Technologies EagleWeb

The Coconino County portal runs Tyler Technologies EagleWeb — a Java servlet-based county recorder system. Key behaviours:

#### 3.1 Session Management

- The server issues a `JSESSIONID` cookie on first page load.
- A Cloudflare `cf_clearance` cookie is also required (browser-managed).
- A `disclaimerAccepted=true` cookie must be present.
- **All three cookies must be present on every request** for the server to respond with data rather than redirecting to the disclaimer page.

#### 3.2 Search Flow (Network-Level)

```
Step 1 — GET  /web/search/DOCSEARCH1213S1
         → server issues JSESSIONID, renders the search form

Step 2 — POST /web/searchPost/DOCSEARCH1213S1
         Content-Type: application/x-www-form-urlencoded
         Body: (see §3.3 payload format)
         → server stores search parameters in the Java session (server-side)
         → returns a JSON/redirect that signals the search is ready

Step 3 — GET  /web/searchResults/DOCSEARCH1213S1?page=1
         Headers: X-Requested-With: XMLHttpRequest, ajaxrequest: true
         → returns HTML fragment with <li class="ss-search-row"> elements
         → repeat for page=2, 3... until no more rows

Step 4 — GET  /web/document/{documentId}?search=DOCSEARCH1213S1
         → returns the document detail page HTML

Step 5 — GET  /web/document-image-pdf/{docId}/{guid}/{filename}-1.pdf?index=1
         → returns the actual PDF bytes
```

#### 3.3 POST Payload Format (CRITICAL)

The search form uses a JavaScript autocomplete widget for document types. The browser sends **one hidden input per selected type** using the `-searchInput` suffix:

```
field_DocNum=
field_rdate_DOT_StartDate=2026-02-13
field_rdate_DOT_EndDate=2026-03-13
field_BothID-containsInput=Contains Any
field_BothID=
field_BookPageID_DOT_Book=
field_BookPageID_DOT_Page=
field_PlattedID_DOT_Subdivision-containsInput=Contains Any
field_PlattedID_DOT_Subdivision=
field_PlattedID_DOT_Lot=
field_PlattedID_DOT_Block=
field_PlattedID_DOT_Tract=
field_LegalCompID_DOT_QuarterSection-containsInput=Contains Any
field_LegalCompID_DOT_QuarterSection=
field_LegalCompID_DOT_Section=
field_LegalCompID_DOT_Township=
field_LegalCompID_DOT_Range=
field_selfservice_documentTypes-searchInput=LIS PENDENS
field_selfservice_documentTypes-searchInput=TRUSTEES DEED UPON SALE
field_selfservice_documentTypes-searchInput=STATE TAX LIEN
... (one per type)
field_selfservice_documentTypes-containsInput=Contains Any
field_selfservice_documentTypes=
```

**Common mistakes that cause empty results or 500 errors:**
- Using `field_selfservice_documentTypes` instead of `field_selfservice_documentTypes-searchInput`
- Omitting the empty scaffold fields (`field_DocNum=`, `field_BothID=`, etc.)
- Sending a `Cookie:` header that overwrites the server-issued `JSESSIONID`

#### 3.4 Playwright vs requests — Why Both Are Needed

| Task | Tool | Why |
|------|------|-----|
| Initial page load + disclaimer | Playwright | Cloudflare JS challenge, dynamic cookie setting |
| Form submission with doc types | Playwright | JS hidden-input injection simplest here |
| Pagination (page 2..N) | requests/urllib | JSESSIONID already valid; no JS needed; much faster |
| Detail page fetch | requests/urllib | No JS rendering needed |
| PDF download | requests/urllib | Simple authenticated GET |

---

## 4. Pipeline Stages — Detailed

### Stage 1: AUTH + SEARCH (Playwright)

**File:** `live_pipeline.py` → `_playwright_search()`

```
1. Launch Chromium (headless by default)
2. Load storage_state.json if it exists (preserves JSESSIONID + cf_clearance)
3. Navigate to /web/search/DOCSEARCH1213S1
4. Click "I Accept" disclaimer if visible
5. Fill #field_rdate_DOT_StartDate with start_date
6. Fill #field_rdate_DOT_EndDate with end_date
7. Inject hidden inputs via page.evaluate():
     for each doc_type:
       create <input type="hidden"
               name="field_selfservice_documentTypes-searchInput"
               value="{doc_type}"
               data-injected-doctype="1">
       append to <form>
     create <input type="hidden"
               name="field_selfservice_documentTypes-containsInput"
               value="Contains Any">
8. Click #searchButton
9. Wait for <li class="ss-search-row"> (results loaded)
10. Parse page-1 HTML → list of records
11. Save storage_state.json (fresh JSESSIONID preserved for pagination)
12. Extract cookie header string from context.cookies()
```

**What the server confirms in its filter summary string:**
```
"Recording Date is between Feb 13, 2026 and Mar 13, 2026 and Document types in
LIS PENDENS, LIS PENDENS RELEASE, TRUSTEES DEED UPON SALE, amended state lien,
notice of trustees sale, release state tax lien, sheriffs deed, state lien,
state tax lien, treasurers deed"
```

---

### Stage 2: PAGINATE (requests)

**File:** `live_pipeline.py` → `_paginate_all_pages()` / `extractor.py` → `fetch_session_results_pages()`

```
1. Read pageCount from page-1 summary
2. For page in range(2, pageCount+1):
     GET /web/searchResults/DOCSEARCH1213S1?page={page}
     Headers:
       Cookie: {JSESSIONID}={value}; cf_clearance={value}; disclaimerAccepted=true
       X-Requested-With: XMLHttpRequest
       ajaxrequest: true
       Accept: */*
     Parse HTML → extend records list
3. Deduplicate by documentId
```

**Important:** The JSESSIONID from Playwright's Stage 1 is bound to the search parameters on the Java server. Using a different or new session for pagination returns different (wrong) results.

---

### Stage 3: CLIENT-SIDE FILTER

**File:** `live_pipeline.py` → `_apply_filter()`

Even though the server filters by document type, it sometimes returns adjacent types or aliases. The client-side filter normalises aliases and keeps only exact target types:

```python
_SERVER_ALIASES = {
    "TRUSTEE'S DEED":         "TRUSTEES DEED UPON SALE",
    "TRUSTEES DEED":          "TRUSTEES DEED UPON SALE",
    "NOTICE OF TRUSTEE'S SALE": "NOTICE OF TRUSTEES SALE",
    "SHERIFF'S DEED":         "SHERIFFS DEED",
    "TREASURER'S DEED":       "TREASURERS DEED",
}
```

---

### Stage 4: REAL-TIME DISPLAY

Prints a live table to stdout as records are collected:

```
   #  FEE / REC #         DATE                 DOC ID          TYPE                        GRANTOR → GRANTEE
────────────────────────────────────────────────────────────────────────────────────────────────────
   1  4035050             03/10/2026 11:06 AM  DOC1870S924     LIS PENDENS RELEASE         DOEGE DEVELOPMENT LLC → CPH 642 RT 66 LLC
   2  4034877             03/06/2026 01:28 PM  DOC1870S779     LIS PENDENS RELEASE         DAVIS JENIFFER → HARVEY JAMES H
  14  4034067             02/24/2026 09:14 AM  DOC1855S677     TRUSTEES DEED UPON SALE     MTC FINANCIAL INC → LAKEVIEW LOAN SERVICING
```

After enrichment, address and principal lines appear below each row:
```
       ↳ address: 3200 North Central Avenue   principal: $285,000.00
```

---

### Stage 5: DETAIL PAGE ENRICHMENT

**File:** `extractor.py` → `fetch_document_detail_fields()`

For each record, fetches `/web/document/{documentId}?search=DOCSEARCH1213S1` and parses:

| Field | How extracted |
|-------|--------------|
| `propertyAddress` | Look for `<strong>Property Address:</strong>` → `<strong>Site Address:</strong>` → regex street pattern |
| `principalAmount` | Look for `<strong>Principal Amount:</strong>` → `<strong>Loan Amount:</strong>` → `_extract_currency_values()` |
| `grantors` | `<strong>Grantor:</strong>` list items |
| `grantees` | `<strong>Grantee:</strong>` list items |
| `subdivision` + `lot` | `Subdivision:` / `Unit/Lot:` labels → fallback address |

---

### Stage 6: PDF DOWNLOAD (GUID-based URL)

**File:** `extractor.py` → `fetch_document_real_pdf_url()` → `fetch_document_pdf()`

See [§5 PDF URL Discovery](#5-pdf-url-discovery-the-critical-fix) for full detail.

Short version: the detail page contains a pdfjs viewer link with a GUID:
```
/web/document-image-pdfjs/DOC1870S924/536801f5-cb31-4c0e-b671-550225b67795/4035050.pdf
```
This is transformed to the direct download URL:
```
/web/document-image-pdf/DOC1870S924/536801f5-cb31-4c0e-b671-550225b67795/4035050-1.pdf?index=1
```

---

### Stage 7: OCR + GROQ

**File:** `extractor.py` → `fetch_document_ocr_and_analysis()`

```
1. fetch_document_pdf() → download PDF bytes to output/documents/
2. extract_text_from_pdf() — run pdftotext on the file
   if text length < 80 chars (image-only scan):
     ocr_pdf() — render pages with pdftoppm → run tesseract on each PNG
3. if use_groq and ocr_text is non-empty:
     analyze_document_text_with_groq():
       POST to https://api.groq.com/openai/v1/chat/completions
    Model: llama-3.3-70b-versatile
       System: "You analyze county recorder OCR text into strict JSON."
       Returns JSON: { summary, parties, property, financials, dates }
       property.address  → propertyAddress
       financials.amount → principalAmount
4. Regex fallback (if Groq unavailable or returned nothing):
     _extract_address_candidates(ocr_text) — street-pattern regex
     _extract_principal_candidates(ocr_text) — "principal amount $X" regex
```

**OCR method hierarchy:**
```
pdftotext (fast, exact)
    → if sparse: tesseract (slower, handles scanned images)
        → Groq Llama-3 (semantic understanding of noisy text)
            → regex patterns (pure deterministic fallback)
```

---

### Stage 8: SAVE

- **CSV:** `output/coconino_pipeline_{timestamp}.csv`
- **JSON:** `output/coconino_pipeline_{timestamp}.json`
- CSV columns: `documentId, recordingNumber, documentType, recordingDate, grantors, grantees, legalDescriptions, propertyAddress, principalAmount, detailUrl, sourceFile, documentUrl, ocrMethod, ocrTextPreview, ocrTextPath, usedGroq, groqError, documentAnalysisError`

---

## 5. PDF URL Discovery — The Critical Fix

### The Problem

The old code generated URLs using this broken pattern:
```
/web/document/servepdf/DEGRADED-{docId}.1.pdf/{recordingNumber}.pdf?index=1
```
This returned **0-byte empty files** — the `DEGRADED-` format is a legacy/internal path that is not publicly accessible.

### The Real URL Format

Every document in the Tyler Technologies EagleWeb system has a **unique GUID** assigned to its image file. The real URL pattern is:

```
/web/document-image-pdf/{docId}/{guid}/{sequenceFilename}-{index}.pdf?index={index}
```

Example:
```
https://eagleassessor.coconino.az.gov:8444/web/document-image-pdf/DOC1870S924/
  536801f5-cb31-4c0e-b671-550225b67795/4035050-1.pdf?index=1
```

### How to Discover the GUID

The GUID is embedded in the document detail page as a pdfjs viewer link:

```html
<a href="/web/document-image-pdfjs/DOC1870S924/
         536801f5-cb31-4c0e-b671-550225b67795/4035050.pdf
         ?allowDownload=true&index=1">
  View Document
</a>
```

### URL Transformation Algorithm

```python
# From the pdfjs link:
pdfjs_path = "/web/document-image-pdfjs/{docId}/{guid}/{seqFile}.pdf"

# The direct download URL is:
download_url = f"/web/document-image-pdf/{docId}/{guid}/{seqFile}-{index}.pdf?index={index}"

# Only difference:
#   document-image-pdfjs  →  document-image-pdf
#   {seqFile}.pdf         →  {seqFile}-{index}.pdf?index={index}
```

### Three-Level Discovery with Fallbacks

```python
def fetch_document_real_pdf_url(document_id, cookie, index=1):

    # Step 1: fetch detail page
    detail_html = GET /web/document/{docId}?search=DOCSEARCH1213S1

    # Primary: parse pdfjs viewer link → transform URL
    if match := re.search(r"/web/document-image-pdfjs/[^/]+/({UUID})/([^?]+)\.pdf", detail_html):
        guid, filename = match.groups()
        return f".../web/document-image-pdf/{docId}/{guid}/{filename}-{index}.pdf?index={index}"

    # Fallback A: direct document-image-pdf link already in detail page
    if match := re.search(r"/web/document-image-pdf/[^/]+/{UUID}/[^?]+\.pdf", detail_html):
        return BASE_URL + match.group(0)

    # Fallback B: fetch the pdfjs viewer page, parse its <iframe src>
    if pdfjs_href found in detail_html:
        pdfjs_html = GET pdfjs_href
        if match := re.search(r'src="(/web/document-image-pdf/[^"]+\.pdf[^"]*)"', pdfjs_html):
            return BASE_URL + match.group(1)

    raise RuntimeError("Could not discover PDF URL")
```

### UUID Regex

```python
_UUID_RE = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
```

---

## 6. OCR + LLM Extraction

### 6.1 pdftotext (Primary)

```bash
pdftotext {pdf_path} -    # outputs text to stdout
```

Works on electronically-generated PDFs. Returns full text with paragraph structure.

### 6.2 Tesseract (Image-Only Fallback)

When `pdftotext` returns fewer than 80 characters (image-only scan):

```bash
# Render each page to PNG at 150 DPI
pdftoppm -png {pdf_path} {working_dir}/page

# OCR each page image
tesseract {working_dir}/page-1.png stdout

# Concatenate all page texts
```

### 6.3 Groq Llama-3 (Semantic Parsing)

**System prompt:**
```
You analyze county recorder OCR text into strict JSON.
Return a JSON object with keys: summary, parties, property, financials, dates, confidenceNotes.
- parties: { grantors: [], grantees: [] }
- property: { legalDescription: "", address: "" }
- financials: { amount: "", loanAmount: "" }
- dates: { recordingDate: "", saleDate: "" }
Do not invent data. Use empty strings or empty arrays when unknown.
```

**User payload:**
```json
{
  "documentId": "DOC1870S924",
  "recordingNumber": "4035050",
  "documentType": "LIS PENDENS RELEASE",
  "ocrText": "RECORDING REQUESTED BY AND WHEN RECORDED MAIL TO:\n..."
}
```

**Model cascade:**
1. `llama-3.3-70b-versatile` (enforced)

### 6.4 Regex Patterns (Pure Fallback)

**Address pattern:**
```python
r"\b\d{1,6}\s+[A-Za-z0-9.#'/-]+(?:\s+[A-Za-z0-9.#'/-]+){1,6}\s+"
r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|..."
```

**Principal amount pattern:**
```python
r"(?:principal(?:\s+amount)?|loan\s+amount|original\s+amount|indebtedness|note\s+amount)"
r"[^$]{0,80}(\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
```

---

## 7. Data Schema

### CSV Output Columns

| Column | Type | Notes |
|--------|------|-------|
| `documentId` | string | e.g. `DOC1870S924` |
| `recordingNumber` | string | Fee number, e.g. `4035050` |
| `documentType` | string | Canonical type name |
| `recordingDate` | string | `MM/DD/YYYY HH:MM AM/PM` |
| `grantors` | string | Pipe-separated: `SMITH JOHN \| SMITH JANE` |
| `grantees` | string | Pipe-separated |
| `legalDescriptions` | string | Pipe-separated |
| `propertyAddress` | string | Street address or subdivision description |
| `principalAmount` | string | Dollar value, e.g. `$285,000.00` |
| `detailUrl` | string | Full URL to detail page |
| `sourceFile` | string | HTML file the record was parsed from |
| `documentUrl` | string | Full URL to PDF download |
| `ocrMethod` | string | `pdftotext` or `tesseract` |
| `ocrTextPreview` | string | First 500 chars of OCR text |
| `ocrTextPath` | string | Path to full OCR text file |
| `usedGroq` | boolean | Whether Groq was called |
| `groqError` | string | Error message if Groq failed |
| `documentAnalysisError` | string | Error message if OCR/download failed |

### Document ID Format

```
DOC{book}{sequence}
e.g. DOC1870S924
     ───────────
     book = 1870
     S    = separator
     seq  = 924
```

---

## 8. Codebase Map

```
conino/
├── live_pipeline.py          # MAIN ENTRY POINT — 8-stage pipeline
├── extractor.py              # All HTTP + parsing + OCR + Groq logic
│   ├── run_live_search()         # POST search + paginate all pages (urllib)
│   ├── fetch_session_results_pages() # Paginate using existing session
│   ├── fetch_document_real_pdf_url() # ★ GUID-based URL discovery
│   ├── fetch_document_pdf()          # Download PDF using real URL
│   ├── fetch_document_detail_fields()# Detail page → address + principal
│   ├── fetch_document_ocr_and_analysis() # OCR + Groq end-to-end
│   ├── ocr_pdf()                    # pdftoppm → tesseract
│   ├── extract_text_from_pdf()      # pdftotext
│   ├── analyze_document_text_with_groq() # Groq Llama-3 call
│   ├── parse_search_results_html()  # Parse <li class="ss-search-row">
│   ├── enrich_records_with_detail_fields() # Batch detail enrichment
│   └── export_csv()                 # Write output CSV
├── fetch_with_session.py     # Alternative entry: Playwright-only pagination
├── run_coconino_cron.sh      # Cron wrapper (auto date range)
├── output/
│   ├── session_state.json    # Playwright persistent auth state
│   ├── coconino_pipeline_*.csv # Pipeline output
│   ├── coconino_pipeline_*.json # Pipeline summary
│   └── documents/           # Downloaded PDFs + OCR working dirs
└── PIPELINE_GUIDE.md         # This file
```

---

## 9. Environment Setup

### 9.1 Python (pyenv 3.10.13)

```bash
# Install pyenv if needed
brew install pyenv
pyenv install 3.10.13
pyenv shell 3.10.13

# Verify
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python --version
# Python 3.10.13
```

### 9.2 Python Packages

```bash
PYBIN=/Users/vishaljha/.pyenv/versions/3.10.13/bin/python

$PYBIN -m pip install playwright requests

# Install Chromium browser
$PYBIN -m playwright install chromium
```

### 9.3 System OCR Tools (macOS)

```bash
brew install poppler      # pdftotext, pdftoppm, pdfinfo
brew install tesseract    # OCR engine

# Verify
which pdftotext pdftoppm tesseract pdfinfo
```

### 9.4 Environment Variables (.env file)

Create `conino/.env`:

```bash
# Required for Groq LLM extraction
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Optional: override Groq model
COCONINO_GROQ_MODEL=llama-3.3-70b-versatile

# Optional: override User-Agent
COCONINO_USER_AGENT=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...

# Optional: pre-set cookie (not needed when session_state.json exists)
# COCONINO_COOKIE=JSESSIONID=xxx; cf_clearance=xxx; disclaimerAccepted=true
```

---

## 10. Configuration Reference

### live_pipeline.py CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--start-date MM/DD/YYYY` | 30 days ago | Recording date range start |
| `--end-date MM/DD/YYYY` | Today | Recording date range end |
| `--pages N` | All pages | Limit pages fetched (for testing) |
| `--ocr-limit N` | 20 | Max documents to run OCR + Groq on |
| `--headful` | headless | Show Playwright browser window |
| `--no-groq` | Groq enabled | Skip Groq, use regex only |
| `--csv-name NAME` | auto timestamp | Custom output CSV filename |
| `--doc-types TYPE...` | TARGET_DOC_TYPES | Override document types |

### Target Document Types List

Defined in `live_pipeline.py` → `TARGET_DOC_TYPES`:

```python
TARGET_DOC_TYPES = [
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
```

Also defined in `extractor.py` → `DEFAULT_DOCUMENT_TYPES` (used by the direct POST path).

---

## 11. Running the Pipeline

### 11.1 Quick Test (1 page, no OCR — ~35 seconds)

```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino

/Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py \
  --start-date "2/13/2026" \
  --end-date "3/13/2026" \
  --pages 1 \
  --ocr-limit 0
```

### 11.2 Quick Test with OCR (3 docs — ~2 minutes)

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py \
  --start-date "2/13/2026" \
  --end-date "3/13/2026" \
  --pages 1 \
  --ocr-limit 3 \
  --no-groq
```

### 11.3 Full Run — Last 30 Days, All Pages, 20 OCR (production)

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py \
  --start-date "2/13/2026" \
  --end-date "3/13/2026" \
  --ocr-limit 20
```

### 11.4 Full Run with Groq LLM

```bash
# Ensure GROQ_API_KEY is set in conino/.env first

/Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py \
  --start-date "2/13/2026" \
  --end-date "3/13/2026" \
  --ocr-limit 50
```

### 11.5 Expected Output

```
[AUTH] Launching Playwright …
[AUTH] Session: reused
[FORM] Start date: 2/13/2026
[FORM] End date:   3/13/2026
[FORM] Injected 10 document-type hidden inputs
[FORM] Search submitted — waiting for results …
[SEARCH] Page 1 via Playwright: 20 records
[AUTH] Cookie extracted (426 chars, 4 cookies)
[SEARCH] Server total: 20 results across 1 pages
[SEARCH] Server filter: Recording Date is between Feb 13, 2026 and Mar 13, 2026 and Document types in LIS PENDENS...
[FILTER] Kept 20 target docs  (removed 0 non-target)

[DISPLAY] 20 target documents found:
   #  FEE / REC #         DATE                 DOC ID          TYPE                        GRANTOR → GRANTEE
────────────────────────────────────────────────────────────────────────────────────────────
   1  4035050             03/10/2026 11:06 AM  DOC1870S924     LIS PENDENS RELEASE         DOEGE DEVELOPMENT LLC → CPH 642 RT 66 LLC
   ...

[DETAIL] Fetching document detail pages for 20 records …
[DETAIL] Done in 29.2s

[OCR] 20 records still need OCR  (running on 3)
[OCR 1/3] DOC1870S924  LIS PENDENS RELEASE … ✓  PDF=146KB  addr=True  amt=False
[OCR 2/3] DOC1870S779  LIS PENDENS RELEASE … ✓  PDF=64KB   addr=True  amt=False
[OCR 3/3] DOC1870S485  LIS PENDENS … ✓  PDF=81KB  addr=True  amt=False

═══════════════════════════════════════════════════════
  ENRICHED RESULTS  (20 documents  |  2/13/2026 → 3/13/2026)
═══════════════════════════════════════════════════════
   1  4035050  ...  DOC1870S924  LIS PENDENS RELEASE  DOEGE DEVELOPMENT LLC → CPH 642...
               ↳ address: Quarter: SE Section: 28...   principal: —
   2  4034877  ...  DOC1870S779  LIS PENDENS RELEASE  DAVIS JENIFFER → HARVEY JAMES H
               ↳ address: 3200 North Central Avenue   principal: —

  Records with address:   16/20
  Records with principal: 0/20

[CSV]  Saved → output/coconino_pipeline_20260314_080000.csv
[JSON] Saved → output/coconino_pipeline_20260314_080000.json
```

---

## 12. Cron Automation

### 12.1 Daily Cron — Every Morning at 8 AM

```bash
crontab -e
```

Add this line:
```cron
0 8 * * * cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino && /Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py --csv-name "daily_$(date +\%Y\%m\%d).csv" >> output/cron.log 2>&1
```

### 12.2 Using the Cron Shell Wrapper

`run_coconino_cron.sh` auto-computes the 30-day date window:

```bash
# macOS date syntax
START_DATE="$(date -v-30d '+%-m/%-d/%Y')"
END_DATE="$(date '+%-m/%-d/%Y')"
```

Environment variables to override:
```bash
COCONINO_START_DATE=2/1/2026    # Override start date
COCONINO_END_DATE=3/1/2026      # Override end date
COCONINO_OCR_PRINCIPAL_LIMIT=50 # How many docs to OCR
COCONINO_HEADFUL=false          # Show browser
GROQ_API_KEY=gsk_xxx            # Groq API key
```

### 12.3 No-Duplicate Guard

To prevent re-processing documents across cron runs, add a `seen_ids.json` file approach:

```python
SEEN_IDS_FILE = OUTPUT_DIR / "seen_doc_ids.json"

def load_seen_ids() -> set[str]:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()

def save_seen_ids(ids: set[str]) -> None:
    SEEN_IDS_FILE.write_text(json.dumps(sorted(ids)))

# In pipeline, before OCR:
seen = load_seen_ids()
new_records = [r for r in records if r["documentId"] not in seen]
# ... process new_records ...
seen.update(r["documentId"] for r in new_records)
save_seen_ids(seen)
```

---

## 13. Troubleshooting

### Issue: HTTP 500 on POST to searchPost

**Cause:** The JSESSIONID is stale — the server-side Java session has expired.

**Fix:** Delete `output/session_state.json` and run again. Playwright will create a fresh session.
```bash
rm conino/output/session_state.json
```

---

### Issue: PDF is 0 bytes / "Document stream is empty"

**Cause:** Old `DEGRADED-*` URL format is being used.

**Fix:** This was the root bug — now fixed. `fetch_document_real_pdf_url()` discovers the GUID-based URL from the detail page. If you still see this, check that the code is using the new function.

---

### Issue: OCR text is empty / very short

**Cause 1:** The PDF is an image scan (no embedded text layer).
**Fix:** `ocr_pdf()` automatically falls back to tesseract when `pdftotext` returns < 80 chars.

**Cause 2:** The PDF download itself failed (0 bytes were written).
**Fix:** Check `documentAnalysisError` column in CSV for the actual error.

---

### Issue: `principalAmount` is always empty

**Expected behaviour for LIS PENDENS:** Lis pendens documents are lawsuit filings — they don't contain a loan amount. Only `TRUSTEES DEED UPON SALE`, `DEED OF TRUST`, and similar transaction documents carry a principal amount.

**Fix:** Expand date range or wait for trustee deed documents to appear.

---

### Issue: Cloudflare block ("cf_clearance" missing)

**Cause:** Cloudflare's JS challenge wasn't solved — usually because the session expired (> 24–48 hours since last run).

**Fix:** Delete `session_state.json` and run with `--headful` once to manually solve the challenge:
```bash
rm output/session_state.json
python live_pipeline.py --headful --pages 1 --ocr-limit 0
```
After this succeeds, headless runs work again.

---

### Issue: search results show all document types, not just target types

**Cause:** The hidden input injection in Playwright failed (form element not found, or timing issue).

**Diagnosis:** Look for `[FORM] Injected 0 document-type hidden inputs` in the log.

**Fix:** Add `page.wait_for_timeout(2000)` before the evaluate call, or use `--headful` to inspect the form state.

---

### Issue: `json.JSONDecodeError` on startup

**Cause:** pyenv stdlib corruption (a rogue leading byte in `json/__init__.py`).

**Fix (one-time):**
```bash
PYBIN=/Users/vishaljha/.pyenv/versions/3.10.13/bin/python
JSON_INIT=$($PYBIN -c "import json; print(json.__file__)")
python3 -c "
data = open('$JSON_INIT', 'rb').read()
data = data.lstrip(b' ')
open('$JSON_INIT', 'wb').write(data)
"
```

---

## 14. AI Prompt Template

Use this prompt when asking an AI to help extend or debug this pipeline:

---

```
CONTEXT: Coconino County, AZ real estate lead scraper

PLATFORM: Tyler Technologies EagleWeb
BASE URL: https://eagleassessor.coconino.az.gov:8444
SEARCH ID: DOCSEARCH1213S1

AUTHENTICATION:
- Playwright (Chromium) handles initial auth, Cloudflare cf_clearance cookie, disclaimer
- Session persisted in output/session_state.json (Playwright storage state)
- JSESSIONID, cf_clearance, disclaimerAccepted=true cookies required on all requests

SEARCH FLOW:
1. Playwright: fill dates + inject hidden inputs for doc types → submit form
   field name for each doc type: "field_selfservice_documentTypes-searchInput"
   operator field: "field_selfservice_documentTypes-containsInput" = "Contains Any"
2. requests (urllib): paginate GET /web/searchResults/DOCSEARCH1213S1?page=N
   with headers: X-Requested-With: XMLHttpRequest, ajaxrequest: true

PDF URL DISCOVERY (critical — old DEGRADED-* URLs return empty files):
1. Fetch detail page: GET /web/document/{docId}?search=DOCSEARCH1213S1
2. Parse for pdfjs link: /web/document-image-pdfjs/{docId}/{UUID}/{file}.pdf
3. Transform to download URL: /web/document-image-pdf/{docId}/{UUID}/{file}-1.pdf?index=1
4. Fallback B: parse pdfjs viewer HTML for <iframe src="/web/document-image-pdf/...">

OCR PIPELINE:
1. pdftotext (primary — text-based PDFs)
2. pdftoppm → tesseract (fallback — image-only scans)
3. Groq Llama-3.3 (semantic parsing of noisy OCR text)
4. Regex patterns (pure deterministic fallback)

MAIN FILES:
- live_pipeline.py  — 8-stage orchestrator
- extractor.py      — all HTTP, parsing, OCR, Groq logic
- output/session_state.json — Playwright persistent auth

TARGET DOC TYPES: LIS PENDENS, LIS PENDENS RELEASE, TRUSTEES DEED UPON SALE,
SHERIFFS DEED, NOTICE OF TRUSTEES SALE, TREASURERS DEED, AMENDED STATE LIEN,
STATE LIEN, STATE TAX LIEN, RELEASE STATE TAX LIEN

OUTPUT FIELDS: documentId, recordingNumber, documentType, recordingDate,
grantors, grantees, legalDescriptions, propertyAddress, principalAmount,
detailUrl, documentUrl, ocrMethod, ocrTextPreview, usedGroq

PYTHON: /Users/vishaljha/.pyenv/versions/3.10.13/bin/python
WORKING DIR: /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino

KNOWN ISSUES ALREADY FIXED:
- DEGRADED-* PDF URL → fixed with GUID discovery
- JSESSIONID overwrite via Cookie header → fixed by pre-seeding session.cookies
- document type POST field → must use "-searchInput" suffix
- pyenv json/__init__.py leading byte corruption → fixed

[DESCRIBE YOUR QUESTION OR CHANGE REQUEST HERE]
```

---

*Last updated: March 14, 2026 — tested live, 20 docs fetched, 3 PDFs OCR'd successfully.*
