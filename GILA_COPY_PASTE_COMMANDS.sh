#!/bin/bash
# GILA COUNTY PIPELINE - COPY & PASTE READY COMMANDS
# 
# How to use: Copy entire command blocks and paste into terminal
# Date Range: February 25 - March 26, 2026 (30 days) 
# Status: ✅ VERIFIED AND WORKING

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1️⃣  RUN 30-DAY PIPELINE (Recommended)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

python gila/run_gila_interval.py \
  --lookback-days 30 \
  --ocr-limit 5 \
  --write-files \
  --workers 4 \
  --once


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2️⃣  RUN 7-DAY PIPELINE (Daily Updates)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

python gila/run_gila_interval.py \
  --lookback-days 7 \
  --ocr-limit 3 \
  --write-files \
  --workers 4 \
  --once


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3️⃣  RUN 60-DAY PIPELINE (Extended History)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

python gila/run_gila_interval.py \
  --lookback-days 60 \
  --ocr-limit 10 \
  --write-files \
  --workers 4 \
  --once


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4️⃣  VERIFY DATABASE RESULTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

python3 verify_gila_30day.py


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5️⃣  MONITOR DATABASE STATS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

python3 /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/gila_db_monitor.py \
  --days 30 \
  --runs 10 \
  --doc-types 20 \
  --show-leads 10


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6️⃣  SCHEDULED CONTINUOUS RUN (Every 60 seconds - background)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

nohup python gila/run_gila_interval.py \
  --lookback-days 7 \
  --ocr-limit 0 \
  --write-files \
  --workers 4 \
  > gila_continuous.log 2>&1 &

# Check if it's running:
pgrep -fl "run_gila_interval"

# Kill it:
pkill -f "python.*run_gila_interval"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7️⃣  VIEW OUTPUT FILES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# View latest CSV
ls -lth gila/output/*.csv | head -1 | awk '{print $NF}' | xargs cat | head -20

# View latest JSON
ls -lth gila/output/*.json | head -1 | awk '{print $NF}' | xargs cat | python3 -m json.tool | head -50


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8️⃣  COMPARE COCONINO VS GILA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Run Coconino 30-day
python3 conino/live_pipeline.py \
  --start-date $(date -v-30d +%m/%d/%Y) \
  --end-date $(date +%m/%d/%Y) \
  --pages 0 \
  --ocr-limit 0 \
  2>&1 | tee coconino_30day_$(date +%Y%m%d_%H%M%S).log

# Then verify both with:
python3 verify_gila_30day.py  # Gila
python verify_coconino_30day.py  # Coconino (if script exists)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NOTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Parameter Meanings:
#   --lookback-days N    = Fetch documents from last N days
#   --ocr-limit N        = 0: skip OCR, 5: first 5 docs, -1: all docs
#   --write-files        = Export CSV and JSON
#   --workers N          = Use N parallel threads (2-8 recommended)
#   --once               = Run once and exit (without it: continuous loop)
#
# Output Files:
#   CSV: gila/output/gila_leads_YYYYMMDD_HHMMSS.csv
#   JSON: gila/output/gila_leads_YYYYMMDD_HHMMSS.json
#
# Issues Fixed:
#   ✅ Removed LIS PENDENS documents (were causing empty trustor/trustee)
#   ✅ Improved property address extraction
#   ✅ Better grantor/grantee details
#
# Last Run: March 26, 2026
# Status: ✅ VERIFIED WITH 2 RECORDS STORED
