# COCHISE COUNTY END-TO-END 20-DAY PRODUCTION PIPELINE - LIVE

**Status:** 🚀 RUNNING IN BACKGROUND  
**Start Time:** 2026-03-25 21:16 UTC  
**PID:** 66579  
**Memory:** ~11 MB (growing as Playwright loads)

---

## 📋 PIPELINE CONFIGURATION

### Model & Processing
```
✅ Model: llama-3.3-70b-versatile (same as Maricopa)
✅ Workers: 4 parallel threads for LLM enrichment
✅ Date Range: 2026-03-04 to 2026-03-24 (20 days)
✅ Groq API: Configured with hosted endpoint
```

### Document Types (6 types)
```
1. NOTICE OF DEFAULT
2. NOTICE OF TRUSTEE SALE
3. LIS PENDENS
4. DEED IN LIEU
5. TREASURERS DEED
6. NOTICE OF REINSTATEMENT
```

---

## 🔍 PIPELINE STAGES (Live Execution)

### Stage 1: Playwright Web Scraping ⏳
- Connects to TheCountyRecorder.com (Cochise site)
- Searches for all document types in 20-day range
- Collects recording numbers and metadata
- **Status:** IN PROGRESS (may take 2-5 minutes depending on network)

### Stage 2: LLM Enrichment 🧠
- Fetches PDF documents for up to 50 records
- Runs Tesseract OCR on scanned PDFs
- Sends OCR text to Groq API (llama-3.3-70b-versatile)
- Extracts: trustor, trustee, beneficiary, principal amount, property address
- **Status:** QUEUED (after Stage 1 completes)

### Stage 3: Detail Fetching 📝
- For remaining records, fetches web-based details
- Compiles manual review flags
- **Status:** QUEUED

### Stage 4: Export 💾
- Generates CSV with 22+ columns
- Generates JSON with full metadata
- Saves to `cochise/output/cochise_20day_YYYYMMDD_HHMMSS.{csv,json}`
- **Status:** QUEUED

### Stage 5: Database Storage 🗄️
- Upserts to PostgreSQL cochise_leads table (if DATABASE_URL set)
- Records in cochise_pipeline_runs (run metadata)
- **Status:** OPTIONAL (not enabled for this run)

---

## 📊 EXPECTED OUTPUTS

### CSV File
```
Location: cochise/output/cochise_20day_*.csv
Columns: 22+ (documentId, recordingNumber, recordingDate, documentType, 
         grantors, grantees, trustor, trustee, beneficiary, principalAmount, 
         propertyAddress, detailUrl, imageUrls, ocrMethod, ocrChars, usedGroq, 
         groqModel, groqError, sourceCounty, analysisError, manualReview, etc.)
```

### JSON File
```json
{
  "meta": {
    "county": "Cochise County, AZ",
    "startDate": "2026-03-04",
    "endDate": "2026-03-24",
    "documentTypes": ["NOTICE OF DEFAULT", ...],
    "recordsFound": <N>,
    "recordsEnriched": <N>,
    "usedGroq": true,
    "timestamp": "2026-03-25T21:16:00"
  },
  "records": [
    {
      "documentId": "...",
      "recordingNumber": "...",
      "trustor": "...",
      "beneficiary": "...",
      ...
    }
  ]
}
```

---

## 🔍 MONITORING IN REAL-TIME

### View Live Logs
```bash
# Watch logs as they stream
tail -f logs/cochise_20day_monitor.log

# Or follow with tail -k to show last 20 lines and wait
tail -k 20 logs/cochise_20day_monitor.log
```

### Check Process Status
```bash
ps aux | grep cochise_20day_simple
```

### When Complete, Check Output
```bash
# Most recent files
ls -lah cochise/output/cochise_20day_*.{csv,json} | tail -2

# Preview CSV
head -5 cochise/output/cochise_20day_*.csv | tail -4

# Count records in JSON
python3 -c "import json; print(len(json.load(open(open('$(ls -t cochise/output/cochise_20day_*.json | head -1)','r').read().strip())).get('records',[])))"
```

---

## 📈 ESTIMATED TIMELINE

| Stage | Duration | Total Time |
|-------|----------|-----------|
| Playwright scraping | 2-5 min | 2-5 min |
| LLM enrichment (50 records) | 3-10 min | 5-15 min |
| Detail fetching | 2-5 min | 10-20 min |
| Export (CSV/JSON) | 1-2 min | 11-22 min |
| **TOTAL** | | **~15-25 minutes** |

**Completion Expected:** 21:25-21:40 UTC (9-24 minutes from start)

---

## 🎯 KEY IMPROVEMENTS FROM MARICOPA PIPELINE

1. ✅ **Same Model:** llama-3.3-70b-versatile (best available)
2. ✅ **Same Prompt:** Cochise-optimized but same structure
3. ✅ **Same Approach:** LLM-first (NO regex fallbacks)
4. ✅ **Parallel Workers:** 4 threads for concurrent enrichment
5. ✅ **Production Ready:** Full error handling and logging
6. ✅ **Clean Codebase:** No debug files, optimized extraction

---

## 💾 DATABASE INTEGRATION (Optional)

To store results to database:

```bash
# Option 1: With DATABASE_URL env var
export DATABASE_URL="postgresql://user:pass@host/db"
python3 cochise/run_cochise_20day_simple.py --model llama-3.3-70b-versatile

# Option 2: Would create tables automatically:
# - cochise_leads (main records)
# - cochise_pipeline_runs (execution metadata)
```

**Note:** Database integration not enabled for this run. To enable, modify the script to call `_connect_db()` and `_upsert_records()` functions.

---

## ✅ WHEN PIPELINE COMPLETES

### Check for Success
```bash
# 1. Verify files exist
ls -lh cochise/output/cochise_20day_*.{csv,json}

# 2. Check record count
tail -5 cochise/output/cochise_20day_*.csv | grep -c ","

# 3. Verify LLM extraction
grep -c "true" cochise/output/cochise_20day_*.csv  # Count usedGroq=true

# 4. Check for errors
grep -i "error" cochise/output/cochise_20day_*.csv | head -3
```

### Export Results
```bash
# Copy to specific location
cp cochise/output/cochise_20day_*.csv ~/Downloads/cochise_leads_$(date +%Y%m%d).csv
cp cochise/output/cochise_20day_*.json ~/Downloads/cochise_leads_$(date +%Y%m%d).json
```

---

## 🚀 NEXT STEPS

1. **Monitor:** Watch `logs/cochise_20day_monitor.log` for progress
2. **Wait:** Process should complete in 15-25 minutes
3. **Verify:** Check CSV and JSON outputs in `cochise/output/`
4. **Analyze:** Open CSV in Excel/Sheets to review extracted leads
5. **Database:** Optional - store to PostgreSQL for persistence

---

## 📞 TROUBLESHOOTING

### If Process Hangs
```bash
# Check if stuck at Playwright stage
ps aux | grep playwright

# Kill and restart
pkill -f cochise_20day_simple.py
python3 cochise/run_cochise_20day_simple.py --model llama-3.3-70b-versatile --workers 4
```

### If Low Record Count
- Date range may not have scheduled foreclosure filings
- Try different document types
- Check if Cochise County website is up: https://www.thecountyrecorder.com/

### If LLM Extraction Fails
- Verify GROQ_API_KEY in .env
- Check network connectivity
- Groq API limits may be exceeded (check API dashboard)

---

**Pipeline Running:** ✅ YES  
**Log File:** `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/cochise_20day_monitor.log`  
**Output Dir:** `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/cochise/output/`

**Last Update:** 2026-03-25 21:16 UTC
