# Maricopa County Production Pipeline - Complete Guide

## Quick Start

### Run Single Day Pipeline
```bash
python3 maricopa/run_single_day_production.py
```

This will:
- Use March 18, 2025 (a date with verified data)
- Process ALL document types
- Download PDFs and OCR text
- Extract property details via LLM
- Generate CSV and JSON outputs
- Display a comprehensive summary

### Run with Specific Date
```bash
python3 maricopa/run_single_day_production.py --date 2025-03-18
```

### Run with Specific Document Code
```bash
python3 maricopa/run_single_day_production.py --doc-code DT
```

### Enable Database Integration
```bash
python3 maricopa/run_single_day_production.py --with-db
```

---

## Understanding the Pipeline

### Architecture

The pipeline has two critical phases:

**Phase 1: API Search & Metadata Fetch**
- Searches Maricopa Recorder public API for recording numbers
- Fetches metadata for each: recording date, document codes, names, page count
- Persists discovered recordings

**Phase 2: Per-Record Processing**
- **Step 1:** Try to download PDF from Maricopa site
- **Step 2:** OCR the PDF using Tesseract
- **Step 3:** Send OCR text to LLM for field extraction
- **Step 4:** Fallback: If no PDF, send minimal metadata to LLM
- **Step 5:** Validate and store results
- **Step 6:** Export to CSV/JSON

### Critical Dependencies

| Component | Status | Impact |
|-----------|--------|--------|
| Tesseract OCR | Must work | If missing, PDF text cannot be extracted |
| Groq LLM | Must work | Field extraction depends on this |
| PDF Downloads | Often works | Some PDFs may be unavailable |
| Maricopa API | Working | Provides search & metadata |

---

## Troubleshooting

### Empty Properties in CSV Output

**Problem:** CSV has all null fields for trustor, address, principal, etc.

**Causes & Solutions:**

1. **PDFs not downloading**
   - Check internet connection
   - Verify Maricopa site is accessible: `https://recorder.maricopa.gov`
   - Some old PDFs may be unavailable

2. **Tesseract Not Installed**
   ```bash
   # macOS
   brew install tesseract
   
   # Ubuntu/Debian
   sudo apt-get install tesseract-ocr
   ```
   Then verify: `tesseract --version`

3. **Groq LLM API Issues**
   - Check GROQ_API_KEY environment variable is set
   - Verify API key is valid
   - Check rate limits: Groq may throttle if you process too many records too fast

4. **Metadata-Only Fallback**
   - When PDFs unavailable, LLM gets sparse metadata
   - This often results in empty fields
   - Solution: Ensure PDFs are downloadable

### API Returns 400 Error

**Problem:** 
```
HTTPError: 400 Client Error: Bad Request
documentCode=NS&beginDate=2026-03-24&endDate=2026-03-25
```

**Solution:**
- This usually means the API doesn't have data for those dates
- Use historical dates (e.g., 2025-03-18)
- Or the document code is invalid for that date range
- Use the diagnostic tool: `python3 maricopa/diagnostic.py`

### No Results Found

**Problem:** Pipeline finds 0 recording numbers

**Possible Causes:**
1. Date range has no filings that day
2. Document code filter is invalid
3. API connectivity issue

**Solution:**
```bash
# Run diagnostic to check all components
python3 maricopa/diagnostic.py

# Try a different date known to have data
python3 maricopa/run_single_day_production.py --date 2025-03-18
```

---

## Valid Document Codes

Common Maricopa County document codes:

| Code | Description |
|------|-------------|
| DT | Deed of Trust |
| NS | Notice of Foreclosure Sale (non-judicial) |
| TR | Trustee Reconveyance |
| UT | Deed of Trust (Uniform) |
| CC | Cancellation of Liens |
| ST | Statutory Documents |
| DD | Deed |
| AS | Assignment of Mortgage/Trust Deed |
| RM | Reconveyance |

To find what codes exist on a specific date, use the diagnostic:
```bash
python3 maricopa/diagnostic.py
```

---

## Output Format

### CSV Output (maricopa_properties.csv)

Columns:
- Document URL - Link to view/download PDF
- Trustor 1 Full Name, First Name, Last Name
- Trustor 2 Full Name, First Name, Last Name
- Property Address
- City, State, Zip
- Address Unit
- Sale Date (MM/DD/YYYY)
- Original Principal Balance
- [Optional metadata: Recording Number, Recording Date, Document Type, Pages]

### JSON Output (maricopa_output.json)

Full record objects with:
```json
{
  "recordingNumber": "20250144711",
  "recordingDate": "3-18-2025",
  "documentCodes": ["N/TR SALE"],
  "names": ["JOHN DOE", "JANE DOE"],
  "pageAmount": 3,
  "trustor_1_full_name": "JOHN DOE",
  "property_address": "123 MAIN ST",
  "address_city": "PHOENIX",
  "address_state": "AZ",
  "address_zip": "85001",
  "original_principal_balance": "250000",
  "sale_date": "03/18/2025",
  "document_url": "https://recorder.maricopa.gov/...",
  ...
}
```

---

## Performance Tuning

### Adjust Worker Threads
```bash
# More workers = faster (but more memory, API rate limiting)
python3 maricopa/run_single_day_production.py --workers 8

# Fewer workers = slower but more stable
python3 maricopa/run_single_day_production.py --workers 2
```

Default: 4 workers (recommended)

### Control Batch Size
By default, processes all records found. To limit:

Edit `run_single_day_production.py` and change:
```python
args = argparse.Namespace(
    limit=100,  # Only process first 100 records
    ...
)
```

---

## Database Integration

### Enable Supabase Storage
```bash
# Set DATABASE_URL environment variable
export DATABASE_URL="postgresql://user:pass@host/database"

# Then run with --with-db
python3 maricopa/run_single_day_production.py --with-db
```

This stores:
- Raw discovered recording numbers
- Document metadata
- Extracted property fields
- Processing pipeline state

### Tables Created

- `discovered_recordings` - List of all recording numbers found
- `documents` - Metadata for each recording
- `extracted_properties` - Extracted trustor, address, principal info
- `extraction_errors` - Any failures or issues
- `pipeline_runs` - Audit trail of pipeline executions

---

## Common Workflows

### Daily Monitoring Dashboard
```bash
# Run every morning
0 6 * * * cd /path/to/project && python3 maricopa/run_single_day_production.py --with-db
```

### Weekly Report
```bash
# Export last 7 days
python3 -m maricopa.scraper --days 7 --document-code ALL --out-csv reports/weekly.csv
```

### Reprocess Specific Dates
```bash
# Force reprocessing (skip cache)
python3 maricopa/run_single_day_production.py --date 2025-03-18 --force
```

---

## Environment Variables

```bash
# Required for LLM extraction
export GROQ_API_KEY="your-api-key-here"

# Optional: Database
export DATABASE_URL="postgresql://..."

# Optional: Proxy
export PROXY_LIST="path/to/proxies.txt"
```

---

## Monitoring & Logs

Logs are written to `logs/` directory with timestamps.

To follow in real-time:
```bash
tail -f logs/maricopa*.log
```

Key log messages to watch:
- "Search API returned X records" - Found recordings
- "OCRing with Tesseract" - PDF processing started
- "Extracting fields via LLM" - Field extraction started
- "Saved X results to" - Success!

---

## Support & Diagnostics

### Run Full Diagnostic
```bash
python3 maricopa/diagnostic.py
```

This tests:
1. API connectivity
2. Available document codes
3. Metadata fetching
4. PDF downloading
5. OCR capability
6. LLM extraction
7. Output file generation

### Enable Debug Logging
```bash
# Run with verbose logging
python3 -m maricopa.scraper --log-level DEBUG --days 1
```

---

## Code Cleanup Status

### Removed
✅ Sanitization functions that blocked valid data
✅ Unused validation logic (lenient_validator.py)
✅ Alternative OCR backends (groq_ocr.py)  
✅ Legacy search implementations
✅ Redundant utility files

### Current Production Code
- `maricopa/scraper.py` - Main pipeline orchestrator
- `maricopa/maricopa_api.py` - API client
- `maricopa/pdf_downloader.py` - PDF fetch
- `maricopa/ocr_pipeline.py` - Tesseract integration
- `maricopa/llm_extract.py` - Groq LLM extraction
- `maricopa/csv_export.py` - Report generation
- `maricopa/db_postgres.py` - Database layer

### Production Entry Points
- `python3 maricopa/run_single_day_production.py` - Recommended
- `python3 -m maricopa.scraper` - Direct (advanced)

---

## Next Steps

1. **Run the diagnostic** to verify all components work
2. **Test with a known date**: `python3 maricopa/run_single_day_production.py`
3. **Check the output**: `open output/maricopa_properties.csv`
4. **Enable database** if you want historical tracking
5. **Schedule daily runs** using cron or equivalent

---

*Last updated: March 25, 2026*
