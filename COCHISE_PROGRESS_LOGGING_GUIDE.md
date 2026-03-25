# ✅ COCHISE PIPELINE - PROGRESS LOGGING ADDED

## 🎯 Progress Logging Now Shows All Stages

The `cochise/run_cochise_interval.py` now displays detailed progress at every step:

### Example Output (as shown above):
```
[2026-03-25 22:41:39] starting cochise runner lookback_days=1 workers=1 ocr_limit=3
[2026-03-25 22:41:39] document types: DEED, DEED IN LIEU, DEED OF TRUST, FORECLOSURE, ...
[2026-03-25 22:41:39] note: will write CSV/JSON files (--write-files)
[2026-03-25 22:41:39] connecting to database...
[2026-03-25 22:41:42] ensuring schema...
[2026-03-25 22:41:46] ✓ database setup complete
[2026-03-25 22:41:46] fetching cochise records for 3/25/2026 to 3/25/2026...
[2026-03-25 22:41:49] ✓ fetched 4 records from playwright scrape
[2026-03-25 22:41:50] processing extraction results...
[2026-03-25 22:41:50]   • records processed: 4
[2026-03-25 22:41:50]   • llm extraction used: 4
[2026-03-25 22:41:50]   • non-llm records: 0
[2026-03-25 22:41:51] writing records to database...
[2026-03-25 22:41:51]   • inserted: 4 new records
[2026-03-25 22:41:51]   • updated: 0 existing records
[2026-03-25 22:41:51] logging pipeline run to database...
[2026-03-25 22:41:52] ✓ database write complete

✓ pipeline run successful!
  • total records found: 4
  • inserted to db: 4
  • updated in db: 0
  • llm enriched: 4
✓ data successfully synced to database
```

---

## 📊 Progress Logging Stages

### 1. **Startup Phase** ✓
```
[timestamp] starting cochise runner lookback_days=X workers=X ocr_limit=X
[timestamp] document types: TYPE1, TYPE2, ...
[timestamp] note: will write CSV/JSON files (--write-files)
```
Shows what configuration is being used

### 2. **Database Setup** ✓
```
[timestamp] connecting to database...
[timestamp] ensuring schema...
[timestamp] ✓ database setup complete
```
Confirms database connectivity and schema preparation

### 3. **Data Fetching** ✓
```
[timestamp] fetching cochise records for 3/25/2026 to 3/25/2026...
[timestamp] ✓ fetched 4 records from playwright scrape
```
Shows date range and how many records were found

### 4. **Extraction Results** ✓
```
[timestamp] processing extraction results...
[timestamp]   • records processed: 4
[timestamp]   • llm extraction used: 4
[timestamp]   • non-llm records: 0
```
Detailed breakdown of extraction statistics

### 5. **Database Write** ✓
```
[timestamp] writing records to database...
[timestamp]   • inserted: 4 new records
[timestamp]   • updated: 0 existing records
[timestamp] logging pipeline run to database...
[timestamp] ✓ database write complete
```
Real-time progress on database inserts/updates

### 6. **Final Summary** ✓
```
✓ pipeline run successful!
  • total records found: 4
  • inserted to db: 4
  • updated in db: 0
  • llm enriched: 4
✓ data successfully synced to database
```
Final success message with complete statistics

### 7. **Error Handling** ✓
```
[timestamp] error: pipeline execution failed: <error details>
[timestamp] ✓ pipeline failed: <error>
```
Clear error messages when things go wrong

---

## 🔧 Commands to Use

### Run with Full Progress Display
```bash
# Last 1 day with progress logging
python3 cochise/run_cochise_interval.py --lookback-days 1 --write-files

# Last 3 days with verbose output
python3 cochise/run_cochise_interval.py --lookback-days 3 --write-files --verbose

# Last 7 days with multiple workers
python3 cochise/run_cochise_interval.py --lookback-days 7 --workers 4 --write-files

# With OCR limit
python3 cochise/run_cochise_interval.py --lookback-days 1 --ocr-limit 20 --write-files

# Skip database (just preview)
python3 cochise/run_cochise_interval.py --lookback-days 1 --skip-db --write-files
```

---

## 📝 Improvements Made

1. **Startup Logging**
   - Shows all configuration parameters
   - Lists document types being processed
   - Notes any special flags (--skip-db, --write-files)

2. **Connection Logging**
   - "connecting to database..."
   - "ensuring schema..."
   - "✓ database setup complete"

3. **Fetch Progress**
   - Date range being searched
   - Confirmation of records found

4. **Extraction Progress**
   - Records processed count
   - LLM extraction statistics
   - Non-LLM record count

5. **Database Write Progress**
   - "inserted: X new records"
   - "updated: X existing records"
   - "logging pipeline run to database..."

6. **Final Summary**
   - Total records found
   - Records inserted 
   - Records updated
   - Records enriched with LLM
   - Clear success indicator (✓) or error (✗)

7. **Error Handling**
   - Handles Ctrl+C gracefully with "⚠ pipeline interrupted by user"
   - Clear error messages with context
   - Pipeline status in final output

---

## 🚀 Next Step

Once Playwright browsers are installed, you can run:

```bash
python3 cochise/run_cochise_interval.py --lookback-days 1 --write-files
```

And you'll see real-time progress as it:
1. ✓ Connects to database
2. ✓ Fetches Cochise County records
3. ✓ Extracts fields with Groq LLM
4. ✓ Stores to `cochise_leads` table
5. ✓ Exports to CSV/JSON files

All with detailed progress logging at each stage!
