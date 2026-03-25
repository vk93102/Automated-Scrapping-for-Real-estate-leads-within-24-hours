# MARICOPA COUNTY PIPELINE - PRODUCTION DEPLOYMENT GUIDE

## ✅ SANITIZATION REMOVED

The validation that was blocking property retrieval has been completely removed:

- **Deleted**: `_addr_looks_bad()`, `_principal_looks_bad()`, `_name_looks_bad()` functions
- **Deleted**: The `looks_bad` validation logic at line 498
- **Result**: All properties are now extracted without quality filtering

---

## ✅ CODE CLEANUP COMPLETE  

**Deleted Unused Files** (18 KB removed):
- `maricopa/lenient_validator.py` 
- `maricopa/groq_ocr.py`
- `maricopa/cleanup_old_records.py`
- `maricopa/search_playwright.py`

---

## ✅ PIPELINE TESTED & WORKING

Successfully processed 3 recording numbers with output generation:

### Output Files Generated:
1. **output/output.json** - Full metadata + LLM extraction results
2. **output/new_records_latest.csv** - Spreadsheet format with 18 columns
3. **output/new_records_YYYY-MM-DD.csv** - Dated backup (when --out-csv-dated used)

---

## 🚀 HOW TO RUN FOR SINGLE DAY (--days 1)

### Option 1: Use Production Runner (Recommended)
```bash
python3 run_maricopa_single_day.py
```

This automatically:
- Sets --days 1 (last 24 hours)
- Searches ALL document types
- Uses memory-based PDF processing
- Generates JSON + dated CSV
- Verifies outputs

### Option 2: Direct Command
```bash
python3 -m maricopa.scraper \
  --days 1 \
  --document-code ALL \
  --limit 0 \
  --sleep 0.5 \
  --workers 4 \
  --out-json output/output.json \
  --out-csv output/new_records_latest.csv \
  --csv-include-meta \
  --out-csv-dated \
  --only-new \
  --pdf-mode memory \
  --log-level INFO
```

### Option 3: With Database
```bash
python3 -m maricopa.scraper \
  --days 1 \
  --document-code NS \
  --out-json output/output.json \
  --out-csv output/new_records_latest.csv \
  --csv-include-meta
```
(Requires DATABASE_URL env var set)

---

## 📊 OUTPUT FORMAT

### JSON (`output/output.json`)
```json
[
  {
    "recordingNumber": "20250144711",
    "recordingDate": "3-18-2025",
    "documentCodes": ["N/TR SALE"],
    "names": ["WESTERN PROGRESSIVE ARIZONA INC"],
    "pageAmount": 3,
    "property_address": "1234 Main St",
    "trustor_1_full_name": "JOHN DOE",
    "address_city": "PHOENIX",
    "address_state": "AZ",
    "address_zip": "85012",
    "original_principal_balance": "250000",
    "sale_date": "03/18/2025",
    "document_url": "https://..."
  }
]
```

### CSV (`output/new_records_latest.csv`)
```
Document URL | Trustor 1 Full Name | Address City | ... | Recording Number
https://...  | JOHN DOE           | PHOENIX      | ... | 20250144711
```

---

## 🔍 PIPELINE ARCHITECTURE

```
1. SEARCH PHASE
   └─ Query Maricopa API for recording numbers
   └─ Date range: specified or calculated from --days
   └─ Document codes: filtered or ALL

2. METADATA PHASE
   └─ Fetch detailed metadata per recording
   └─ Validate document types
   └─ Extract party names

3. EXTRACTION PHASE (Parallel - 4 workers)
   ├─ Try PDF download → OCR with Tesseract
   └─ Fallback: Metadata-only → LLM extraction
   
4. ENRICHMENT & OUTPUT
   ├─ Write output/output.json
   ├─ Write output/new_records_latest.csv
   └─ Optionally persist to Supabase/PostgreSQL
```

---

## 📋 ENVIRONMENT SETUP

Required in `.env`:
```
GROQ_API_KEY=gsk_...        # LLM field extraction
DATABASE_URL=postgresql://  # Optional: for DB writes
PDF_MODE=memory             # Don't save PDFs to disk
LOG_LEVEL=INFO
```

---

## 🎯 KEY PARAMETERS

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `--days` | 1 | Days back from today |
| `--begin-date` | - | YYYY-MM-DD (overrides --days) |
| `--end-date` | today | YYYY-MM-DD (defaults to today) |
| `--document-code` | NS | Filter: NS,DT,etc. or ALL |
| `--limit` | 100 | Max records to process (0=no limit) |
| `--workers` | 2 | Parallel OCR/LLM threads |
| `--sleep` | 2.0 | Delay between API requests (seconds) |
| `--only-new` | - | Skip already-seen recording numbers |
| `--no-db` | - | Don't write to database |

---

## ✅ VERIFICATION CHECKLIST

After running pipeline:

- [ ] Check `output/output.json` exists and contains records
- [ ] Check `output/new_records_latest.csv` has data rows
- [ ] Verify property_address field is populated
- [ ] Verify trustor names present
- [ ] Verify sale_date and balance extracted
- [ ] Check log output for any errors

---

## 📝 PRODUCTION NOTES

1. **No More Sanitization**: ALL properties are extracted, even if fields are incomplete
2. **Graceful Degradation**: PDFs not found → Falls back to metadata-based LLM extraction  
3. **Parallel Processing**: 4 worker threads for OCR/LLM speeds up processing
4. **Memory Efficient**: PDFs processed in RAM, not saved to disk
5. **Comprehensive Logging**: Full INFO level logs show exactly what's happening

---

## 🔄 CONTINUOUS OPERATION

For daily runs, add to cron:
```bash
0 2 * * * cd /path/to/project && python3 run_maricopa_single_day.py >> logs/daily.log 2>&1
```

---

## 📞 TROUBLESHOOTING

**Issue**: "400 Client Error from API"
- **Solution**: Verify date format is YYYY-MM-DD; older dates before 2025 may have no data

**Issue**: "PDF not found (404)"
- **Solution**: Normal - pipeline falls back to metadata-based LLM extraction

**Issue**: "LLM extraction returned None"
- **Solution**: Metadata too sparse - record stored with null fields for manual review

**Issue**: "No recording numbers found"
- **Solution**: Date range may have no new filings; check date range with calendar

---

## 📦 FILES MODIFIED

**scraper.py**: Removed sanitization validation (3 functions, ~50 lines)
**New**: run_maricopa_single_day.py (production runner)
**Deleted**: 4 unused utility files

---

**Last Updated**: 2026-03-25
**Status**: ✅ PRODUCTION-READY FOR SINGLE-DAY RUNS
