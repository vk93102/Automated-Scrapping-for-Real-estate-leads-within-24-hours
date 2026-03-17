# La Paz County — Draft Overview & Implementation Plan

## 1) What the attached document tells us

La Paz county is **not** Tyler EagleWeb like Coconino/Gila. It is an ASP.NET site:
- Base: `https://www.thecountyrecorder.com/Search.aspx`
- Intermediate page: `https://www.thecountyrecorder.com/Introduction.aspx`
- Search results: `https://www.thecountyrecorder.com/Results.aspx`
- Detail page: `https://www.thecountyrecorder.com/Document.aspx?DK=<document_key>`
- Image endpoint: `https://www.thecountyrecorder.com/ImageHandler.ashx?DK=<document_key>&PN=<page_number>`

### UI flow observed
1. Open Search page
2. Click **Continue** (`MainContent_Button1`)
3. Select **State = Arizona**, **County = La Paz**
4. Click **Yes, I Accept** (`MainContent_searchMainContent_ctl01_btnAccept`)
5. Redirect to Introduction
6. Go to Search → **Document**
7. Fill date range + document type, submit
8. Parse Results page
9. Open each document detail by `DK`
10. Click/View Image or use `ImageHandler.ashx` directly
11. OCR image pages → LLM extraction for principal amount + address + parties

---

## 2) Endpoint and payload understanding

The search is form-post based (ASP.NET WebForms), so payload includes dynamic hidden fields such as:
- `__VIEWSTATE`
- `__EVENTVALIDATION`
- `__VIEWSTATEGENERATOR`

Documented field names from your notes include:
- `ctl00$ctl00$MainContent$searchMainContent$ctl00$tbDateStart`
- `ctl00$ctl00$MainContent$searchMainContent$ctl00$tbDateEnd`
- `ctl00$ctl00$MainContent$searchMainContent$ctl00$cboDocumentType`
- `ctl00$ctl00$MainContent$searchMainContent$ctl00$btnSearchDocuments`

State/county selector fields:
- `ctl00$ctl00$ContentPlaceHolder_SelectCounty$ctl00$cboStates`
- `ctl00$ctl00$ContentPlaceHolder_SelectCounty$ctl00$cboCounties`

Because hidden fields and control IDs can change per session, **browser automation is the safest primary path**.

---

## 3) Recommended technical approach (working plan)

## Phase A — Session bootstrap (Playwright only)
- Start Chromium
- Handle Continue → county select → accept disclaimer
- Land on Document search screen
- Save storage state/cookies for reuse in same run

## Phase B — Search execution
- Fill date start/end and doc type
- Submit search using page button click (not raw POST first)
- Wait for results page/table
- Capture HTML snapshot for parser stability tests

## Phase C — Result parsing
Extract per row:
- `documentId` (or DK link)
- `recordingDate`
- `documentType`
- names (grantor/grantee if visible)

## Phase D — Detail + image retrieval
- Visit `Document.aspx?DK=...`
- Resolve image/page links
- Download image pages from `ImageHandler.ashx?DK=...&PN=...`

## Phase E — OCR + extraction
- OCR all relevant pages
- LLM/rules extract:
  - `principalAmount`
  - `propertyAddress`
  - `trustor`
  - `trustee`
  - `beneficiary`

## Phase F — Export + dedupe
- Output JSON + CSV in `lapaz/output/`
- Keep idempotency by `documentId`/`DK`
- Add lightweight retry/failure tracking

---

## 4) Proposed outputs (target schema)

- `recordingNumber` / `documentId`
- `recordingDate`
- `documentType`
- `grantors`
- `grantees`
- `principalAmount`
- `propertyAddress`
- `trustor`
- `trustee`
- `beneficiary`
- `sourceUrl`
- `imageUrls`

---

## 5) Risks and mitigations

1. ASP.NET dynamic hidden fields change frequently  
   → Mitigation: prefer Playwright-driven submit over handcrafted raw POST.

2. Session/cookie gating  
   → Mitigation: persist storage state and reuse inside run.

3. Occasional UI reorder/selector drift  
   → Mitigation: multi-selector fallback by ID/name/text.

4. Image pagination unknown upfront  
   → Mitigation: probe `PN=1..N` until non-image response/404.

---

## 6) Implementation layout (inside `lapaz/` only)

When you approve coding, create only these in `lapaz/`:
- `lapaz/extractor.py` (HTML parsing)
- `lapaz/search_playwright.py` (browser session + search)
- `lapaz/pdf_or_image_downloader.py` (ImageHandler fetching)
- `lapaz/live_pipeline.py` (end-to-end runner)
- `lapaz/command.txt` (repeatable run command)
- `lapaz/output/` (artifacts)

---

## 7) Immediate next step

Build a **minimal smoke pipeline** first:
- date range = last 7 days
- 1 document type (NOTICE OF TRUSTEE SALE)
- parse first page results only
- fetch 1 document image
- OCR + extract principal/address

If smoke passes, scale to full doc-type set and pagination.
