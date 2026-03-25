# COCONINO PIPELINE - EXECUTABLE COMMANDS (Copy & Paste)

## 🎯 MOST COMMONLY USED COMMANDS

All commands below are ready to copy and paste directly. Just select, copy, and paste into your terminal.

---

## Command 1: STANDARD 3-DAY RUN (RECOMMENDED)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Fetches last 3 days → Processes all with OCR → Uses Groq LLM → Saves CSV + DB

---

## Command 2: WEEKLY RUN (7 DAYS)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-7d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_weekly_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Fetches last 7 days → Processes all documents

---

## Command 3: DAILY RUN (YESTERDAY ONLY)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-1d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_daily_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Fetches only yesterday's documents (good for daily cron jobs)

---

## Command 4: LIMIT OCR TO 10 DOCUMENTS
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit 10 2>&1 | tee coconino_limit10_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Fetches 3 days → Processes only first 10 with OCR (saves time/resources)

---

## Command 5: SKIP OCR (FASTEST - METADATA ONLY)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit -1 2>&1 | tee coconino_fast_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Fetches 3 days → Skips all OCR processing → Very fast, metadata only

---

## Command 6: QUICK TEST (FIRST PAGE + 5 DOCS)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --pages 1 --ocr-limit 5 2>&1 | tee coconino_test_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Fetches 1st page only → Processes only 5 docs → Good for testing (~2-3 min)

---

## Command 7: DEBUG MODE (SHOW BROWSER WINDOW)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --headful --ocr-limit 3 2>&1 | tee coconino_debug_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Shows browser window → Processes only 3 docs → Good for debugging

---

## Command 8: SKIP GROQ (CUSTOM ENDPOINT ONLY)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --no-groq 2>&1 | tee coconino_no_groq_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Processes OCR → Skips Groq LLM → Uses custom endpoint only

---

## Command 9: SPECIFIC DATE RANGE
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date 03/23/2026 --end-date 03/26/2026 2>&1 | tee coconino_specific_$(date +%Y%m%d_%H%M%S).log
```
**What it does:** Specify exact dates (replace with your dates in MM/DD/YYYY format)

---

## Command 10: PRODUCTION MODE (CRON FRIENDLY)
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-1d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit 0 2>&1 | tee -a coconino_production_$(date +%Y%m).log
```
**What it does:** Yesterday only → All documents → Appends to log (good for cron)

---

---

## 📋 PARAMETER QUICK REFERENCE

### Date Parameters
```
--start-date DATE       Start date for search (format: MM/DD/YYYY)
--end-date DATE         End date for search (format: MM/DD/YYYY)

Date calculation shortcuts (macOS):
$(date +%m/%d/%Y)              = today
$(date -v-1d +%m/%d/%Y)        = yesterday
$(date -v-3d +%m/%d/%Y)        = 3 days ago
$(date -v-7d +%m/%d/%Y)        = 7 days ago
$(date -v-30d +%m/%d/%Y)       = 30 days ago
```

### Processing Parameters
```
--ocr-limit N           Number of documents to process with OCR
                        0 = all documents
                        -1 = skip OCR entirely
                        5, 10, 20, etc = process N documents

--pages N               Max pages to fetch
                        1 = page 1 only
                        5 = pages 1-5
                        (omit) = all pages

--headful               Show browser window (for debugging)
                        --headful = visible
                        (omit) = headless/hidden (default)

--no-groq               Skip Groq LLM processing
                        --no-groq = skip
                        (omit) = use Groq (default)

--csv-name FILE         Custom CSV filename
--doc-types TYPE        Filter specific document types
```

---

## 🖥️ RUNNING IN BACKGROUND (For Long Processes)

### Start pipeline in background:
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) > coconino_background.log 2>&1 &
```

### In another terminal, monitor progress:
```bash
tail -f coconino_background.log
```

### Check if process is still running:
```bash
ps aux | grep "python3 conino/live_pipeline.py" | grep -v grep
```

### Kill if stuck:
```bash
pkill -f "python3 conino/live_pipeline.py"
```

---

## ✅ AFTER RUNNING - VERIFY RESULTS

### Check CSV was created:
```bash
ls -lh conino/output/coconino_pipeline_*.csv | tail -1
```

### Count records in CSV:
```bash
wc -l conino/output/coconino_pipeline_*.csv | tail -1
```

### View CSV column names:
```bash
head -1 conino/output/coconino_pipeline_*.csv | tr ',' '\n' | nl
```

### Verify database storage:
```bash
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
total = cur.fetchone()[0]
print(f"✅ Total COCONINO records in DB: {total}")
cur.execute('SELECT documentId, documentType, recordingDate FROM public.maricopa_properties WHERE county = %s ORDER BY recordingDate DESC LIMIT 3', ('COCONINO',))
print("\n📋 3 Most Recent Records:")
for row in cur.fetchall():
    print(f"  • {row[0]} | {row[1]} | {row[2]}")
cur.close()
PYEOF
```

---

## 🎯 WORKFLOW EXAMPLE

Here's a complete step-by-step workflow:

```bash
# Step 1: Navigate to project
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours

# Step 2: Set Python version
pyenv shell 3.10.13

# Step 3: Run the pipeline (pick ONE command from above)
python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log

# (Wait for "✅ COMPLETE" message)

# Step 4: Verify CSV exists
ls -lh conino/output/coconino_pipeline_*.csv | tail -1

# Step 5: Check records in database
python3 << 'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM public.maricopa_properties WHERE county='COCONINO'")
print(f"✅ Total records: {cur.fetchone()[0]}")
cur.close()
PYEOF

# Step 6: Copy CSV to main output directory
LATEST_CSV=$(ls -t conino/output/coconino_pipeline_*.csv | head -1)
cp "$LATEST_CSV" "output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"
echo "✅ CSV ready at: output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"
```

---

## 📊 UNDERSTANDING THE OUTPUT

When you run a command, you'll see progress like this:

```
[AUTH] Launching Playwright...
[FORM] Search submitted — waiting for results...
[SEARCH] Page 1 via Playwright: 10 records
[FILTER] Kept 10 target docs
[DETAIL] Fetching document detail pages...
✓ PDF enrichment: 10 records
[OCR 1/10] DOC1882S873 ... ✓ PDF=94KB
[OCR 2/10] DOC1882S859 ... ✓ PDF=126KB
...processing continues...
[CSV] Generating export...
[DB] Storing in Supabase...
✅ COMPLETE
```

**This means:**
- ✅ Documents fetched
- ✅ OCR processed
- ✅ CSV created
- ✅ Database stored

All stages completed successfully!

---

## 🚨 TROUBLESHOOTING

**Q: Pipeline not starting?**
```bash
# Make sure Python is correct
python3 --version

# Check if dependencies installed
python3 -c "import conino.live_pipeline; print('✅ Module works')"

# Set Python version
pyenv shell 3.10.13
```

**Q: Getting "No documents found"?**
```bash
# Check dates are correct
# Try a wider date range
# Visit: https://eagleassessor.coconino.az.gov/ to verify website is up
```

**Q: Process seems stuck?**
```bash
# In another terminal:
tail -f coconino_pipeline_run_*.log

# To kill:
pkill -f "python3 conino/live_pipeline.py"
```

**Q: CSV not created?**
```bash
# Check permissions
ls -l conino/output/

# Check disk space
df -h /Users/vishaljha/

# View errors in log
grep -i "error\|exception" coconino_pipeline_run_*.log
```

---

## 📞 QUICK START (Absolute Minimum)

If you just want to run it with default settings:

```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y)
```

That's it! It will:
- ✅ Search last 3 days
- ✅ Process all documents
- ✅ Save to CSV + Database
- ✅ Show all progress

---
