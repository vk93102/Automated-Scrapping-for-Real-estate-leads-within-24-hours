# 🚀 COCONINO COUNTY PIPELINE - COMPLETE COMMAND REFERENCE
# All parameters explained with real-world examples

## ============================================================================
## PARAMETER DEFINITIONS
## ============================================================================

### COCONINO PIPELINE PARAMETERS

Parameter              Type      Default             Description
─────────────────────────────────────────────────────────────────────────────

--start-date          STRING    3 days ago          Start date for document search
                                (MM/DD/YYYY)        Format: MM/DD/YYYY
                                                    Example: 03/23/2026

--end-date            STRING    Today               End date for document search
                                (MM/DD/YYYY)        Format: MM/DD/YYYY
                                                    Example: 03/26/2026

--pages               INTEGER   None (fetch all)    Maximum result pages to fetch
                                                    Example: --pages 1 (only page 1)
                                                    Example: --pages 5 (fetch 5 pages)

--ocr-limit           INTEGER   20                  Max documents to process with OCR
                                                    0 = process all documents
                                                    -1 = skip OCR entirely
                                                    20 = first 20 documents (default)

--headful             FLAG      False               Show browser window while running
                                                    --headful (visible browser)
                                                    (omit for headless mode - default)

--no-groq             FLAG      False               Skip Groq LLM processing
                                                    --no-groq (skip LLM)
                                                    (omit to use LLM - default)

--csv-name            STRING    None (auto)         Custom CSV filename
                                                    Example: --csv-name my_report.csv

--doc-types           STRING    All target types    Filter for specific document types
                      (LIST)                        Example: --doc-types "LIS PENDENS"
                                                    Example: --doc-types "DEED" "LIEN"


## ============================================================================
## COMMAND EXAMPLES
## ============================================================================

### EXAMPLE 1: STANDARD 3-DAY SEARCH (MOST COMMON)
# Fetches last 3 days of documents, processes all with OCR, uses Groq LLM
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours
python3 conino/live_pipeline.py \
    --start-date 03/23/2026 \
    --end-date 03/26/2026 \
    2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log

### OUTPUT:
# [AUTH] Launching Playwright...
# [SEARCH] Page 1 via Playwright: 10 records
# [FILTER] Kept 10 target docs
# [DETAIL] Fetching document detail pages...
# [OCR 1/10] DOC1882S873 LIS PENDENS ... ✓ PDF=94KB
# [OCR 2/10] DOC1882S859 LIS PENDENS ... ✓ PDF=126KB
# ...processing continues...
# CSV saved: coconino_pipeline_20260326_014647.csv
# ✅ COMPLETE


### ---------------------------------------------------------------------------
### EXAMPLE 2: USING DATE CALCULATION (TODAY AND 3 DAYS BACK)
# Uses bash date arithmetic to calculate dates automatically
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log

# Breakdown:
#   $(date -v-3d +%m/%d/%Y)    = 3 days ago (03/23/2026 if today is 03/26)
#   $(date +%m/%d/%Y)          = today (03/26/2026)
#   2>&1                        = redirect errors to output
#   | tee <filename>           = save output to file AND display on screen


### ---------------------------------------------------------------------------
### EXAMPLE 3: LAST 7 DAYS OF DOCUMENTS
# Searches for documents from the past 7 days
python3 conino/live_pipeline.py \
    --start-date $(date -v-7d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    2>&1 | tee coconino_7day_pipeline_$(date +%Y%m%d_%H%M%S).log

# Parameters explained:
#   -v-7d     = go back 7 days
#   -v-30d    = go back 30 days
#   -v-1d     = go back 1 day (yesterday)
#   (no flag) = today


### ---------------------------------------------------------------------------
### EXAMPLE 4: LIMIT OCR TO FIRST 10 DOCUMENTS ONLY
# Useful for testing or when running low on Groq credits
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 10 \
    2>&1 | tee coconino_pipeline_ocr10_$(date +%Y%m%d_%H%M%S).log

# Parameters:
#   --ocr-limit 10     = OCR only first 10 documents
#   --ocr-limit 0      = OCR all documents (no limit)
#   --ocr-limit 1      = OCR only first document (for testing)
#   --ocr-limit 20     = default: OCR first 20 documents


### ---------------------------------------------------------------------------
### EXAMPLE 5: SKIP OCR/LLM PROCESSING ENTIRELY
# Just fetch and extract basic metadata, skip heavy processing
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit -1 \
    2>&1 | tee coconino_fast_pipeline_$(date +%Y%m%d_%H%M%S).log

# Parameters:
#   --ocr-limit -1     = skip OCR completely
#   --no-groq          = skip Groq LLM processing
# (Combined) = fastest pipeline, metadata only


### ---------------------------------------------------------------------------
### EXAMPLE 6: FIRST PAGE ONLY (QUICK TEST)
# Fetch only 1 page of results for quick testing
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --pages 1 \
    --ocr-limit 5 \
    2>&1 | tee coconino_test_pipeline_$(date +%Y%m%d_%H%M%S).log

# Parameters:
#   --pages 1          = fetch only page 1
#   --pages 5          = fetch pages 1-5
#   (omit --pages)     = fetch all pages


### ---------------------------------------------------------------------------
### EXAMPLE 7: VISIBLE BROWSER MODE (DEBUGGING)
# Show the browser window while running, useful for debugging
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --headful \
    --ocr-limit 5 \
    2>&1 | tee coconino_debug_pipeline_$(date +%Y%m%d_%H%M%S).log

# Parameters:
#   --headful          = show browser window
#   (omit --headful)   = headless mode (default, faster, no window)


### ---------------------------------------------------------------------------
### EXAMPLE 8: SKIP GROQ LLM (USE CUSTOM ENDPOINT ONLY)
# Process with OCR but skip the Groq fallback
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --no-groq \
    2>&1 | tee coconino_no_groq_pipeline_$(date +%Y%m%d_%H%M%S).log

# Parameters:
#   --no-groq          = skip Groq LLM processing
#   (omit --no-groq)   = use Groq LLM (default)


### ---------------------------------------------------------------------------
### EXAMPLE 9: SPECIFIC DOCUMENT TYPES ONLY
# Filter for only LIS PENDENS documents
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --doc-types "LIS PENDENS" \
    2>&1 | tee coconino_lis_pendens_$(date +%Y%m%d_%H%M%S).log

# Other examples:
#   --doc-types "DEED"
#   --doc-types "TRUSTEES DEED UPON SALE"
#   --doc-types "LIEN"
#   --doc-types "SHERIFFS DEED"
#   --doc-types "STATE TAX LIEN"
#
# Multiple types:
#   --doc-types "LIS PENDENS" "DEED"


### ---------------------------------------------------------------------------
### EXAMPLE 10: CUSTOM CSV FILENAME
# Save CSV with custom name instead of timestamped name
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --csv-name "coconino_march_26.csv" \
    2>&1 | tee coconino_pipeline_run_$(date +%Y%m%d_%H%M%S).log

# Parameters:
#   --csv-name "custom_name.csv"  = custom CSV filename
#   (omit --csv-name)             = auto-generated timestamp name


### ---------------------------------------------------------------------------
### EXAMPLE 11: COMBINATION - FULL CONFIGURATION
# Complete pipeline with all options configured
python3 conino/live_pipeline.py \
    --start-date $(date -v-7d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --pages 2 \
    --ocr-limit 15 \
    --doc-types "LIS PENDENS" "DEED" \
    --no-groq \
    2>&1 | tee coconino_full_config_$(date +%Y%m%d_%H%M%S).log

# This will:
#   • Search last 7 days
#   • Fetch up to 2 pages of results
#   • OCR first 15 documents
#   • Filter for LIS PENDENS and DEED types
#   • Skip Groq LLM processing
#   • Save to timestamped log file


### ---------------------------------------------------------------------------
### EXAMPLE 12: PRODUCTION SCHEDULED RUN (CRON)
# Optimized for running unattended via cron job
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0 \
    2>&1 | tee -a /var/log/coconino_pipeline_$(date +%Y%m%d).log

# Parameters for production:
#   -v-1d                = last 1 day (previous day)
#   --ocr-limit 0        = process all documents found
#   tee -a filename      = append to log file (don't overwrite)
#   /var/log/            = log to system logs


## ============================================================================
## RUNNING IN BACKGROUND (FOR LONG OPERATIONS)
## ============================================================================

### Option A: Run in background, monitor with tail
# Start the pipeline
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    > coconino_pipeline_run.log 2>&1 &

# In another terminal, monitor progress:
tail -f coconino_pipeline_run.log

# When done, check the final result:
tail -50 coconino_pipeline_run.log


### Option B: Run in background with process tracking
# Start pipeline and save PID
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    > coconino_pipeline_run.log 2>&1 &
PIPELINE_PID=$!

# Wait for it to complete
wait $PIPELINE_PID
echo "✅ Pipeline completed with exit code: $?"


### Option C: Run with nohup (survives terminal close)
nohup python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    > coconino_pipeline_run.log 2>&1 &

# View progress anytime:
tail -f coconino_pipeline_run.log

# Check if still running:
ps aux | grep "python3 conino/live_pipeline.py" | grep -v grep


## ============================================================================
## MONITORING & DEBUGGING COMMANDS
## ============================================================================

# View all output in real-time
tail -f coconino_pipeline_run.log

# Count documents found
grep -c "\[SEARCH\]" coconino_pipeline_run.log

# Count OCR processed
grep -c "✓  PDF=" coconino_pipeline_run.log

# Find errors
grep -i "error\|failed\|❌" coconino_pipeline_run.log

# View search results summary
grep "\[SEARCH\]" coconino_pipeline_run.log

# View filter summary
grep "\[FILTER\]" coconino_pipeline_run.log

# View OCR progress
grep "OCR" coconino_pipeline_run.log | tail -20

# Check if CSV was created
ls -lh conino/output/coconino_pipeline_*.csv | tail -1

# Count records in generated CSV
wc -l conino/output/coconino_pipeline_*.csv | tail -1

# View CSV header (column names)
head -1 conino/output/coconino_pipeline_*.csv | tail -1 | tr ',' '\n' | nl

# View first data row
head -2 conino/output/coconino_pipeline_*.csv | tail -1


## ============================================================================
## COMMON WORKFLOWS
## ============================================================================

### WORKFLOW 1: DAILY RUN (Fetch yesterday's documents)
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    2>&1 | tee coconino_daily_$(date +%Y%m%d_%H%M%S).log


### WORKFLOW 2: WEEKLY BACKFILL (Last 7 days)
python3 conino/live_pipeline.py \
    --start-date $(date -v-7d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0 \
    2>&1 | tee coconino_weekly_$(date +%Y%m%d_%H%M%S).log


### WORKFLOW 3: QUICK TEST (First page, 5 docs)
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --pages 1 \
    --ocr-limit 5 \
    2>&1 | tee coconino_test_$(date +%Y%m%d_%H%M%S).log


### WORKFLOW 4: FULL BACKFILL (30 days, all docs)
python3 conino/live_pipeline.py \
    --start-date $(date -v-30d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0 \
    --headful \
    2>&1 | tee coconino_backfill_$(date +%Y%m%d_%H%M%S).log


### WORKFLOW 5: PRODUCTION RUN (Optimized for cron)
python3 conino/live_pipeline.py \
    --start-date $(date -v-1d +%m/%d/%Y) \
    --end-date $(date +%m/%d/%Y) \
    --ocr-limit 0 \
    2>&1 | tee -a coconino_production_$(date +%Y%m).log


## ============================================================================
## TROUBLESHOOTING
## ============================================================================

# If pipeline seems hung, monitor in another terminal:
tail -f coconino_pipeline_run.log

# Kill a stuck process:
pkill -f "python3 conino/live_pipeline.py"

# Check if Playwright browser is open:
ps aux | grep -i playwright

# Clear browser cache/cookies (if needed):
rm -rf conino/output/*cookie* conino/output/session_state.json

# Verify environment variables:
echo "DATABASE_URL=$DATABASE_URL"
echo "GROQ_API_KEY=$GROQ_API_KEY"
echo "GROQ_LLM_ENDPOINT_URL=$GROQ_LLM_ENDPOINT_URL"


## ============================================================================
## REFERENCE: DATE CALCULATION
## ============================================================================

# macOS date command formats for --start-date/--end-date:

$(date +%m/%d/%Y)              # Today: 03/26/2026
$(date -v-1d +%m/%d/%Y)        # Yesterday: 03/25/2026
$(date -v-3d +%m/%d/%Y)        # 3 days ago: 03/23/2026
$(date -v-7d +%m/%d/%Y)        # 7 days ago: 03/19/2026
$(date -v-30d +%m/%d/%Y)       # 30 days ago: 02/24/2026
$(date -v-1m +%m/%d/%Y)        # 1 month ago: 02/26/2026

# Alternative (explicit dates):
03/23/2026
03/26/2026
01/01/2026


## ============================================================================
## Environment Variables (Optional)
## ============================================================================

# Already configured:
export DATABASE_URL="postgresql://..."
export GROQ_API_KEY="gsk_..."
export GROQ_LLM_ENDPOINT_URL="http://fccskc4c448c4wskkwsk8kkk.31.220.21.129.sslip.io/api/v1/llm/extract"

# These are automatically detected from .env file


## ============================================================================
