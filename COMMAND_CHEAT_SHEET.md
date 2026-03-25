# 📋 COCONINO PIPELINE - QUICK COMMAND CHEAT SHEET

## ✅ MOST COMMON COMMAND (Copy & Paste Ready)

### Standard 3-Day Pipeline Run
```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && \
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log
```

**What it does:**
- ✅ Fetches documents from last 3 days
- ✅ Processes all documents with OCR
- ✅ Uses Groq LLM for enrichment
- ✅ Saves to CSV + Database
- ✅ Shows all progress in real-time

---

## 🔧 QUICK PARAMETER REFERENCE

### Date Ranges
```bash
# Last 1 day (yesterday)
--start-date $(date -v-1d +%m/%d/%Y) --end-date $(date +%m/%d/%Y)

# Last 3 days (default)
--start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y)

# Last 7 days
--start-date $(date -v-7d +%m/%d/%Y) --end-date $(date +%m/%d/%Y)

# Last 30 days
--start-date $(date -v-30d +%m/%d/%Y) --end-date $(date +%m/%d/%Y)

# Specific dates
--start-date 03/23/2026 --end-date 03/26/2026
```

### OCR Processing Limits
```bash
--ocr-limit 0      # Process ALL documents (no limit)
--ocr-limit 5      # Process first 5 documents only
--ocr-limit 10     # Process first 10 documents
--ocr-limit 20     # Default: first 20 documents
--ocr-limit -1     # Skip OCR entirely
```

### Other Options
```bash
--pages 1          # Fetch only first page
--pages 5          # Fetch first 5 pages
--headful          # Show browser window (debugging)
--no-groq          # Skip Groq LLM processing
--csv-name FILE    # Custom CSV filename
--doc-types TYPE   # Filter specific document types
```

---

## 📊 COMMAND EXAMPLES BY USE CASE

### 1️⃣  Standard Daily Run
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    2>&1 | tee coconino_daily_$(date +%Y%m%d).log
```

### 2️⃣  Weekly Backfill (All 7 Days)
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-7d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0 \
    2>&1 | tee coconino_weekly_$(date +%Y%m%d).log
```

### 3️⃣  Quick Test (5 Documents)
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --pages 1 \
    --ocr-limit 5 \
    2>&1 | tee coconino_test_$(date +%Y%m%d).log
```

### 4️⃣  Fast Run (No OCR)
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit -1 \
    2>&1 | tee coconino_fast_$(date +%Y%m%d).log
```

### 5️⃣  Debug Mode (Show Browser + Limit Docs)
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --headful \
    --ocr-limit 3 \
    2>&1 | tee coconino_debug_$(date +%Y%m%d).log
```

### 6️⃣  Production Run (For cron jobs)
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0 \
    2>&1 | tee -a /var/log/coconino_$(date +%Y%m%d).log
```

---

## 🎯 PARAMETER BREAKDOWN

### What Each Parameter Does

```
--start-date DATE     → When to START searching for documents
--end-date DATE       → When to STOP searching for documents
--ocr-limit N         → How many documents to process with PDF/OCR
--pages N             → How many pages of results to fetch
--headful             → Show browser window (for debugging)
--no-groq             → Skip the Groq LLM processing step
--csv-name FILE       → What to name the output CSV file
--doc-types TYPE      → Filter by document type (LIS PENDENS, DEED, etc.)
```

### Date Format Examples

```
03/23/2026            → Specific date (March 23, 2026)
$(date +%m/%d/%Y)     → Today
$(date -v-1d +%m/%d/%Y)   → 1 day ago
$(date -v-3d +%m/%d/%Y)   → 3 days ago
$(date -v-7d +%m/%d/%Y)   → 7 days ago
$(date -v-30d +%m/%d/%Y)  → 30 days ago
```

---

## 📈 REAL-TIME PROGRESS MONITORING

### View progress while running (in another terminal)
```bash
tail -f coconino_pipeline_run_*.log
```

### Count documents being processed
```bash
grep "OCR [0-9]" coconino_pipeline_run_*.log | wc -l
```

### See if it's done
```bash
ps aux | grep "python3 conino/live_pipeline.py" | grep -v grep
```

### View final results
```bash
tail -50 coconino_pipeline_run_*.log
```

---

## 🗂️  OUTPUT FILES

After running, check:

```bash
# Generated CSV file
ls -lh conino/output/coconino_pipeline_*.csv

# Generated JSON file
ls -lh conino/output/coconino_pipeline_*.json

# Count records in CSV
wc -l conino/output/coconino_pipeline_*.csv

# View CSV headers
head -1 conino/output/coconino_pipeline_*.csv | tr ',' '\n' | nl
```

---

## 🚫 TROUBLESHOOTING

### If stuck or hung:
```bash
# Kill the process
pkill -f "python3 conino/live_pipeline.py"

# Check if still running
ps aux | grep conino | grep -v grep
```

### If getting errors:
```bash
# Check environment variables
echo "DATABASE_URL=$DATABASE_URL"
echo "GROQ_API_KEY=$GROQ_API_KEY"

# View error logs
grep -i "error\|failed" coconino_pipeline_run_*.log
```

### Clear cache if needed:
```bash
# Remove saved cookies and session state
rm -rf conino/output/*cookie* conino/output/session_state.json
```

---

## 💾 COPY TO DATABASE

Pipeline automatically stores in Supabase, but to verify:

```bash
python3 << 'EOF'
import os, psycopg2
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
print(f"Total COCONINO records: {cur.fetchone()[0]}")
cur.close()
EOF
```

---

## 🔄 COMPLETE WORKFLOW

### Copy & Paste This Entire Sequence:

```bash
# 1. Setup
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours
pyenv shell 3.10.13

# 2. Run pipeline
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log

# 3. (In separate terminal - monitor progress)
tail -f coconino_pipeline_run_*.log

# 4. When done - verify CSV created
ls -lh conino/output/coconino_pipeline_*.csv | tail -1

# 5. Copy to main output directory
LATEST=$(ls -t conino/output/coconino_pipeline_*.csv | head -1)
cp "$LATEST" "output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"

# 6. Check records in database
python3 << 'EOF'
import os, psycopg2
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
print(f"✅ Total records in DB: {cur.fetchone()[0]}")
cur.close()
EOF
```

---

## 🎓 UNDERSTANDING THE PARAMETERS

When you run:
```bash
python3 conino/live_pipeline.py \
    --start-date 03/23/2026 \
    --end-date 03/26/2026 \
    --ocr-limit 20 \
    --pages 2
```

This means:
- 🗓️  Search for documents between March 23-26, 2026
- 📄 Fetch up to 2 pages of results
- 🔤 Process up to 20 documents with OCR
- 💾 Save everything to CSV + Database

---

## 📞 COMMON QUESTIONS

**Q: What does `--ocr-limit` do?**
A: Controls how many PDF documents to process. 0=all, 5=first 5, -1=none

**Q: Why use `tee` in the command?**
A: Saves output to a file AND displays it on screen at the same time

**Q: How do I run it in the background?**
A: Add `&` at the end, then use `tail -f logfile` to monitor

**Q: What if the browser window gets stuck?**
A: It's headless by default (no window). Use `--headful` to see it.

**Q: How long does it take?**
A: ~30+ minutes for 10 documents (depends on PDF size and OCR processing)

---

## 🎯 RECOMMENDED SETTINGS BY USE CASE

### Daily Automated Run (Cron)
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0
```

### Testing
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --pages 1 \
    --ocr-limit 3
```

### Development/Debugging
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --headful \
    --ocr-limit 5
```

### Full Backfill
```bash
python3 conino/live_pipeline.py \
    --start-date $(date -v-30d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0
```

---
