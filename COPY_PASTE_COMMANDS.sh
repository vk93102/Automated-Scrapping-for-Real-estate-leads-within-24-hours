#!/bin/bash

# 🚀 COCONINO PIPELINE - COPY & PASTE READY COMMANDS
# Each command below is complete and ready to use - just copy and paste!

echo "COCONINO COUNTY PIPELINE - COMMAND TEMPLATES"
echo "=============================================="
echo ""
echo "📋 Choose your use case and copy the command below:"
echo ""

# ============================================================================
# COMMAND 1: STANDARD 3-DAY RUN (MOST COMMON)
# ============================================================================
cat << 'EOF'
1️⃣  STANDARD 3-DAY RUN (Processing last 3 days)
   ✅ Fetches 3 days of documents
   ✅ Processes all with OCR
   ✅ Uses Groq LLM
   ✅ Saves CSV + Database

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 2: LAST 7 DAYS
# ============================================================================
cat << 'EOF'
2️⃣  WEEKLY RUN (Last 7 days worth of documents)
   ✅ Fetches 7 days of documents
   ✅ Processes all with OCR
   ✅ Useful for weekly summaries

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-7d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_weekly_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 3: YESTERDAY ONLY (DAILY)
# ============================================================================
cat << 'EOF'
3️⃣  DAILY RUN (Yesterday only)
   ✅ Fetches only yesterday's documents
   ✅ Quick and fast
   ✅ Good for cron jobs

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-1d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) 2>&1 | tee coconino_daily_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 4: WITH OCR LIMIT
# ============================================================================
cat << 'EOF'
4️⃣  WITH OCR LIMIT (Process only 10 documents)
   ✅ Fetches 3 days of documents
   ✅ Processes only first 10 with OCR
   ✅ Good for testing without full processing

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit 10 2>&1 | tee coconino_limit10_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 5: SKIP OCR (FAST)
# ============================================================================
cat << 'EOF'
5️⃣  SKIP OCR (Fastest mode - metadata only)
   ✅ Fetches 3 days of documents
   ✅ Skips PDF OCR processing
   ✅ Very fast - only metadata extracted
   ✅ Use --ocr-limit -1

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit -1 2>&1 | tee coconino_fast_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 6: ONLY FIRST PAGE
# ============================================================================
cat << 'EOF'
6️⃣  TEST MODE (First page only + 5 documents)
   ✅ Fetches only first page of results
   ✅ Processes only first 5 documents
   ✅ Good for quick testing
   ✅ Fast execution (~2-3 minutes)

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --pages 1 --ocr-limit 5 2>&1 | tee coconino_test_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 7: DEBUGGING MODE (SHOW BROWSER)
# ============================================================================
cat << 'EOF'
7️⃣  DEBUG MODE (Show browser window)
   ✅ Displays browser window while running
   ✅ Useful for debugging issues
   ✅ Process only 3 documents
   ✅ Use --headful flag

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --headful --ocr-limit 3 2>&1 | tee coconino_debug_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 8: SKIP GROQ LLM
# ============================================================================
cat << 'EOF'
8️⃣  SKIP GROQ LLM (Custom endpoint only)
   ✅ Processes OCR but skips Groq
   ✅ Uses custom LLM endpoint only
   ✅ Process all documents (--ocr-limit 0)

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --no-groq 2>&1 | tee coconino_no_groq_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 9: SPECIFIC DATES
# ============================================================================
cat << 'EOF'
9️⃣  SPECIFIC DATE RANGE (Manual dates)
   ✅ Use explicit dates instead of relative
   ✅ Date format: MM/DD/YYYY
   ✅ Example: 03/23/2026 to 03/26/2026

COPY THIS COMMAND (modify dates as needed):

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date 03/23/2026 --end-date 03/26/2026 2>&1 | tee coconino_specific_dates_$(date +%Y%m%d_%H%M%S).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# COMMAND 10: PRODUCTION/CRON
# ============================================================================
cat << 'EOF'
🔟 PRODUCTION RUN (For automated cron jobs)
   ✅ Optimized for unattended execution
   ✅ Last 1 day of documents
   ✅ All documents processed (--ocr-limit 0)
   ✅ Append to log file (don't overwrite)

COPY THIS COMMAND:

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && python3 conino/live_pipeline.py --start-date $(date -v-1d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit 0 2>&1 | tee -a coconino_production_$(date +%Y%m).log

────────────────────────────────────────────────────────────────────────────────

EOF

# ============================================================================
# PARAMETER EXPLANATIONS
# ============================================================================
cat << 'EOF'

📝 PARAMETER EXPLANATION GUIDE

--start-date DATE
   └─ Format: MM/DD/YYYY
   └─ Examples:
      • $(date -v-1d +%m/%d/%Y)    = yesterday
      • $(date -v-3d +%m/%d/%Y)    = 3 days ago
      • $(date -v-7d +%m/%d/%Y)    = 7 days ago
      • 03/23/2026                 = specific date

--end-date DATE
   └─ Format: MM/DD/YYYY
   └─ Usually: $(date +%m/%d/%Y) (today)

--ocr-limit N
   └─ How many documents to process with OCR
   └─ Values:
      • -1  = Skip OCR entirely (fastest)
      • 0   = Process ALL documents (no limit)
      • 5   = Process first 5 documents
      • 10  = Process first 10 documents
      • 20  = Default: first 20 documents

--pages N
   └─ Max pages to fetch
   └─ Values:
      • 1   = Only first page
      • 5   = First 5 pages
      • (omit) = all pages

--headful
   └─ Show browser window while running
   └─ Useful for debugging
   └─ (omit for headless/no window - default)

--no-groq
   └─ Skip Groq LLM processing
   └─ Only use custom endpoint
   └─ (omit to use Groq - default)

2>&1 | tee FILENAME.log
   └─ Save output to file AND display on screen
   └─ Allows real-time monitoring


════════════════════════════════════════════════════════════════════════════════

RUNNING IN BACKGROUND (Long Operations)

To run the pipeline in the background and monitor separately:

STEP 1 - Start pipeline:
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && \
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    > coconino_background.log 2>&1 &

STEP 2 - Monitor in another terminal:
tail -f coconino_background.log

STEP 3 - Check if completed:
ps aux | grep "python3 conino/live_pipeline.py" | grep -v grep

════════════════════════════════════════════════════════════════════════════════

AFTER RUNNING - VERIFY RESULTS

1. Check if CSV was created:
   ls -lh conino/output/coconino_pipeline_*.csv | tail -1

2. Count records in CSV:
   wc -l conino/output/coconino_pipeline_*.csv | tail -1

3. View CSV headers:
   head -1 conino/output/coconino_pipeline_*.csv | tr ',' '\n' | nl

4. Check database:
   python3 << 'PYEOF'
   import os, psycopg2
   conn = psycopg2.connect(os.getenv('DATABASE_URL'))
   cur = conn.cursor()
   cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
   print(f"✅ Total COCONINO records: {cur.fetchone()[0]}")
   cur.close()
   PYEOF

════════════════════════════════════════════════════════════════════════════════

COMMON ISSUES & SOLUTIONS

If pipeline seems stuck:
  → Monitor with: tail -f coconino_pipeline_run_*.log
  → Kill process: pkill -f "python3 conino/live_pipeline.py"

If getting permission denied:
  → Run: chmod +x conino/live_pipeline.py
  → Or use: python3 conino/live_pipeline.py (with python3 prefix)

If DATABASE_URL not found:
  → Check: echo $DATABASE_URL
  → Source .env: source .env
  → Or: export DATABASE_URL="your_url"

If no documents found:
  → Check dates are correct
  → Try a wider date range
  → Check Coconino website is up

════════════════════════════════════════════════════════════════════════════════

QUICK REFERENCE - MOST USEFUL COMMANDS

Setup Python:
  pyenv shell 3.10.13

Run standard pipeline:
  python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y)

Run with limited OCR:
  python3 conino/live_pipeline.py --start-date $(date -v-3d +%m/%d/%Y) --end-date $(date +%m/%d/%Y) --ocr-limit 10

Quick test:
  python3 conino/live_pipeline.py --pages 1 --ocr-limit 5

Monitor log:
  tail -f coconino_pipeline_run_*.log

Check if running:
  ps aux | grep conino | grep python

Kill stuck process:
  pkill -f "python3 conino"

════════════════════════════════════════════════════════════════════════════════
EOF
