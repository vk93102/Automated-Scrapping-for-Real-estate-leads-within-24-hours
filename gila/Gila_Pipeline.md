# Gila County AZ — Real Estate Lead Scraper
## Architecture & Operations Guide

---

## 1. Overview

This scraper targets the **Tyler Technologies EagleWeb** public records portal for Gila County, AZ. It collects foreclosure and distressed-property leads by searching for 9 document types (Trustees Deeds, Lis Pendens, State Tax Liens, etc.), downloads the associated PDFs, OCR-reads them, and exports structured CSV + JSON output.

```
Portal  →  Playwright (login + search + maximize results)
        →  requests  (pagination — greedy, no record cap)
        →  Detail page parse (grantors, grantees, recording date, legal)
        →  PDF download → OCR (pdftotext / Tesseract)
        →  Groq LLM enrichment (optional)
        →  CSV + JSON export
```

---

## 2. Target Portal

| Property         | Value |
|-----------------|-------|
| County           | Gila County, AZ |
| Portal           | Tyler Technologies EagleWeb (jQuery Mobile SPA) |
| Base URL         | `https://selfservice.gilacountyaz.gov` |
| Search ID        | `DOCSEARCH2242S1` |
| Search URL       | `/web/search/DOCSEARCH2242S1` |
| Results URL      | `/web/searchResults/DOCSEARCH2242S1?page=N&_={timestamp}` |
| Detail URL       | `/web/document/{DOC_ID}?search=DOCSEARCH2242S1` |
| PDF URL pattern  | `/web/document-image-pdf/{DOC_ID}/{token}/{recording_number}-1.pdf?index=1` |

**Why Playwright?** The portal renders its search form via jQuery Mobile (SPA). Plain `requests` cannot submit the form — it always returns HTTP 500. A real browser (Playwright Chromium) is required for the initial search.

---

## 3. Document Types Scraped (9 total)

| Code in Portal              | Category          |
|-----------------------------|-------------------|
| LIS PENDENS                 | Foreclosure signal |
| TRUSTEES DEED               | Foreclosure completed |
| SHERIFFS DEED               | Court-ordered sale |
| NOTICE OF TRUSTEES SALE     | Upcoming auction |
| TREASURERS DEED             | Tax foreclosure |
| AMENDED STATE LIEN          | State tax debt |
| STATE LIEN                  | State tax debt |
| STATE TAX LIEN              | State tax debt |
| RELEASE STATE TAX LIEN      | Lien released |

---

## 4. File Structure

```
gila/
├── __init__.py
├── extractor.py          ← CORE ENGINE  (all scraping logic)
├── run_demo.py           ← Live demo entry point (CLI)
├── live_pipeline.py      ← Production pipeline orchestrator
├── requirements.txt      ← Python dependencies
├── .env.example          ← Environment variable template
├── ARCHITECTURE.md       ← This file
└── output/
    ├── gila_demo_YYYYMMDD_HHMMSS.csv   ← Exported leads
    ├── gila_demo_YYYYMMDD_HHMMSS.json  ← Full structured output
    ├── session_state.json              ← Playwright browser state (auto-reused)
    └── documents/
        ├── DOC*.pdf                    ← Downloaded PDFs
        └── DOC*_ocr.txt               ← Extracted OCR text
```

---

## 5. Pipeline Stages (run_demo.py)

```
Stage 1-3  playwright_search()
           ├── Launch Chromium (headless)
           ├── Accept disclaimer
           ├── Fill date range form fields
           ├── Inject 9 document-type hidden inputs via JS
           ├── Set results-per-page SELECT to MAXIMUM value  ← key: avoids 20-record cap
           ├── Click #searchButton
           ├── Wait for .ss-search-row (results visible)
           ├── Parse page-1 HTML → records[]
           └── Return (cookie_str, page1_records, summary)

Stage 4    _make_requests_session(cookie_str)
           └── Convert Playwright cookies → requests.Session

Stage 4b   fetch_all_pages()   [GREEDY MODE]
           ├── Fire on_record() callback for page-1 records (real-time stream)
           ├── page = 2, consecutive_empty = 0
           ├── Loop:
           │   ├── GET /web/searchResults/...?page=N&_={ts}
           │   ├── If new records found → append, consecutive_empty = 0
           │   └── If empty × 2 consecutive → STOP  ← no pageCount trust
           └── Returns deduplicated all_records[]

Stage 5    Client-side filter
           └── Remove any records not in DEFAULT_DOCUMENT_TYPES

Stage 6    enrich_record_with_detail()   (all 46 records, parallel-ish)
           ├── GET /web/document/{DOC_ID}?search=DOCSEARCH2242S1
           └── Parse: recordingDate, grantors[], grantees[], legalDescriptions[],
                       propertyAddress, principalAmount, pdfjs_href

Stage 7    enrich_record_with_ocr()      (all records, ocr_limit=0)
           ├── discover_pdf_url()  (pdfjs href → real PDF URL)
           ├── download_pdf()      (GET with session, 403-safe)
           ├── extract_ocr_text()  pdftotext → Tesseract fallback
           ├── analyze_with_groq() (optional LLM, requires GROQ_API_KEY)
           └── Regex fallback for address + principal from OCR text

Stage 8    export_csv() + export_json()
           └── gila/output/gila_demo_YYYYMMDD_HHMMSS.{csv,json}
```

---

## 6. Key Design Decisions

### 6.1 No 20-Record Cap — Two Strategies

The EagleWeb portal defaults to **20 results per page**. Two complementary fixes ensure all records are retrieved:

**Strategy A — Maximize per-page BEFORE submit (Playwright):**
```python
# In playwright_search() — set SELECT to last (largest) option before clicking Search
page.evaluate("""
    const sel = document.querySelector(
        'select[id*=resultsPerPage],select[name*=resultsPerPage],...'
    );
    if (sel && sel.options.length > 0) {
        sel.value = sel.options[sel.options.length - 1].value;
        sel.dispatchEvent(new Event('change', {bubbles: true}));
    }
""")
```
→ Portal returns 46–100 records on page 1 instead of 20.

**Strategy B — Greedy pagination (fetch_all_pages):**
```python
# Never trust pageCount from server (it lies: reports 1 even with more pages)
# Keep fetching pages 2, 3, … until 2 consecutive empty pages
while True:
    records, _ = fetch_results_page(session, page)
    if new_count == 0:
        consecutive_empty += 1
        if consecutive_empty >= 2: break
    else:
        consecutive_empty = 0
    page += 1
```
→ Catches any records that spilled to page 2+ despite maximization.

### 6.2 Detail Page Parsing — Tyler EagleWeb HTML Pattern

Fields are in `<table>` cells, NOT immediately after `<strong>`:
```html
<strong>Recording Date:</strong>
</div>
<div>02/23/2026 10:32:17 AM</div>   ← value is in SIBLING div
```

The parser uses `(?:\s*<[^>]+>)*` to skip any number of intermediate tags.

### 6.3 OCR Convention

| `--ocr-limit` | Behaviour |
|--------------|-----------|
| `0` (default) | OCR ALL documents |
| `-1`          | Skip OCR entirely (metadata only) |
| `N` (e.g. 10) | OCR first N documents only |

### 6.4 PDF Discovery (Two-Hop)

1. Detail page contains a `pdfjs` viewer href like `/web/document-image-pdfjs/.../file.pdf`
2. GET that URL (inside iframe) reveals the real direct PDF download URL
3. Download with session cookies (handles 403 gracefully)

---

## 7. Run Commands

### 7.1 Full Run — All Records, All OCR (Standard)

```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours

/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/run_demo.py \
  --start-date "10/1/2025" \
  --end-date   "3/14/2026" \
  > /tmp/gila_run.log 2>&1 &

echo "PID=$!  →  tail -f /tmp/gila_run.log"
```

### 7.2 Watch Live Logs

```bash
tail -f /tmp/gila_run.log
```

### 7.3 Wait for Completion + Print Full Log

```bash
while pgrep -f "gila/run_demo" > /dev/null; do sleep 5; done \
  && echo "=== DONE ===" \
  && cat /tmp/gila_run.log
```

### 7.4 Last 30 Days (Rolling)

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/run_demo.py \
  > /tmp/gila_run.log 2>&1 &
# (no --start-date / --end-date → defaults to last 30 days)
```

### 7.5 Custom Date Range

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/run_demo.py \
  --start-date "1/1/2026" \
  --end-date   "3/14/2026" \
  > /tmp/gila_run.log 2>&1 &
```

### 7.6 Skip OCR (Fast — Metadata + Detail Only)

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/run_demo.py \
  --start-date "1/1/2026" \
  --end-date   "3/14/2026" \
  --ocr-limit -1 \
  > /tmp/gila_run.log 2>&1 &
```

### 7.7 Cap OCR at 10 Documents

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/run_demo.py \
  --start-date "1/1/2026" \
  --end-date   "3/14/2026" \
  --ocr-limit 10 \
  > /tmp/gila_run.log 2>&1 &
```

### 7.8 With Groq LLM Enrichment

```bash
GROQ_API_KEY=gsk_xxxxxxxxxxxx \
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/run_demo.py \
  --start-date "10/1/2025" \
  --end-date   "3/14/2026" \
  --groq \
  > /tmp/gila_run.log 2>&1 &
```

### 7.9 Inspect Output CSV

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python3 -c "
import csv, pathlib
csvf = sorted(pathlib.Path('gila/output').glob('gila_demo_*.csv'))[-1]
rows = list(csv.DictReader(open(csvf)))
print('File:', csvf.name, '|', len(rows), 'rows')
for i, r in enumerate(rows, 1):
    addr = r.get('propertyAddress', '').replace(chr(10), ' ')[:40]
    print(i, r['documentId'], r['documentType'][:18],
          repr(addr[:35]), r.get('principalAmount',''))
"
```

### 7.10 Production Pipeline (live_pipeline.py)

```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python gila/live_pipeline.py \
  --start-date "10/1/2025" \
  --end-date   "3/14/2026" \
  > /tmp/gila_pipeline.log 2>&1 &
```

---

## 8. CLI Reference (run_demo.py)

| Argument        | Default       | Description |
|----------------|---------------|-------------|
| `--start-date` | 30 days ago   | Search start date `MM/DD/YYYY` |
| `--end-date`   | Today         | Search end date `MM/DD/YYYY` |
| `--pages`      | `0` (all)     | Max result pages to fetch (`0` = greedy/unlimited) |
| `--ocr-limit`  | `0` (all)     | OCR: `0`=all docs, `-1`=skip, `N`=cap at N |
| `--groq`       | off           | Enable Groq LLM analysis (requires `GROQ_API_KEY`) |

---

## 9. Output CSV Columns

| Column              | Source         | Description |
|--------------------|----------------|-------------|
| `documentId`        | Search results | Internal portal ID (e.g. `DOC2346S1408`) |
| `recordingNumber`   | Detail page    | Official recording number (e.g. `2026-001775`) |
| `documentType`      | Search + detail | e.g. `TRUSTEES DEED` |
| `recordingDate`     | Detail page    | Date/time filed (e.g. `02/23/2026 10:32:17 AM`) |
| `grantors`          | Detail page    | Pipe-separated grantor names |
| `grantees`          | Detail page    | Pipe-separated grantee names |
| `legalDescriptions` | Detail page    | Parcel / subdivision legal descriptions |
| `propertyAddress`   | OCR + detail   | Street address extracted from PDF |
| `principalAmount`   | OCR + detail   | Loan / lien amount |
| `detailUrl`         | Derived        | Direct link to portal detail page |
| `documentUrl`       | Discovered     | Direct PDF download link |
| `ocrMethod`         | OCR            | `pdftotext` or `tesseract` |
| `ocrTextPreview`    | OCR            | First 500 chars of OCR text |
| `ocrTextPath`       | OCR            | Local path to full OCR `.txt` file |
| `usedGroq`          | Groq           | Whether LLM enrichment was run |

---

## 10. Environment Variables (.env)

```dotenv
GROQ_API_KEY=gsk_xxxxxxxxxxxx   # Optional — enables LLM enrichment
```

---

## 11. Dependencies

```
playwright          # Chromium browser automation
requests            # HTTP session for pagination + PDF download
beautifulsoup4      # HTML parsing
pytesseract         # Tesseract OCR fallback
Pillow              # Image processing for Tesseract
groq                # Groq LLM client (optional)
python-dotenv       # .env loader
```

System requirement: `tesseract` and `poppler` (for `pdftotext`) must be installed:
```bash
brew install tesseract poppler   # macOS
```

Install Playwright browsers:
```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python -m playwright install chromium
```

---

## 12. Verified Run Results (2026-03-14)

| Metric                     | Value |
|---------------------------|-------|
| Date range                 | Oct 1 2025 → Mar 14 2026 |
| Records fetched            | **46** |
| PDFs downloaded            | 45 / 46 |
| OCR successful             | 45 / 46 |
| Records with address       | 40 / 46 |
| Records with principal amt | 23 / 46 |
| Records with recordingDate | 46 / 46 *(after parser fix)* |
| Records with grantors      | 46 / 46 *(after parser fix)* |

---

## 13. Known Limitations

| Issue | Notes |
|-------|-------|
| 1 doc has no PDF link | `DOC2346S999` (LIS PENDENS) — portal shows no downloadable PDF |
| Some addresses blank | Property address not on cover page of some PDFs |
| Session expires | Playwright re-authenticates automatically on each run |
| Groq rate limits | Slow with large batches; use `--ocr-limit N` to control |
