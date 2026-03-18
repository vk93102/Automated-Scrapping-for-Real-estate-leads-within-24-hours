#!/bin/bash
# County Pipeline Management Commands
# ==================================
# This script provides comprehensive commands for managing all 8 Arizona county pipelines
# with proper OCR/LLM extraction, database storage, and logging.
#
# All commands assume you're in the project root directory.

PROJECT_ROOT="/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"

# ============================================================================
# SECTION 1: RUNNING 30-DAY BACKFILLS WITH FULL OCR/LLM EXTRACTION
# ============================================================================
# These commands extract the last 30 days of records with:
# - Full OCR text extraction from document images
# - Groq LLM parsing (trustor, trustee, address, principal amount, etc.)
# - Direct database insertion
#
# WARNING: These are long-running operations (15-60 minutes per county)
# Each command processes ALL documents found in the date range

# Run 30-day backfill for Graham county (includes OCR/LLM extraction)
cd "$PROJECT_ROOT" && python3 graham/backfill_30days.py

# Run 30-day backfills for ALL 8 counties sequentially
# (Estimated total time: 2-8 hours depending on document volume and OCR performance)
for county in graham greenlee cochise gila navajo lapaz "SANTA CRUZ" conino; do
  echo "Starting $county backfill..."
  cd "$PROJECT_ROOT"
  if [ -f "$county/backfill_30days.py" ]; then
    python3 "$county/backfill_30days.py" 2>&1 | tee "logs/${county}_backfill_$(date +%Y%m%d_%H%M%S).log"
  fi
done

# ============================================================================
# SECTION 2: RUNNING SCHEDULED CRON JOBS FOR SPECIFIC COUNTIES
# ============================================================================
# These commands trigger immediate extraction for each county
# Set environment variables to control OCR behavior:
#  - OCR_LIMIT=0:  Process all documents with OCR + Groq LLM (RECOMMENDED for backfill)
#  - OCR_LIMIT=-1: Skip OCR/LLM (fastest, use only if data already populated)
#  - OCR_LIMIT=N:  Process first N documents with OCR (for testing)

# Run scheduled Graham extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/graham" && bash run_graham_cron.sh

# Run scheduled Greenlee extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/greenlee" && bash run_greenlee_cron.sh

# Run scheduled Cochise extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/cochise" && bash run_cochise_cron.sh

# Run scheduled Gila extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/gila" && bash run_gila_cron.sh

# Run scheduled Navajo extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/navajo" && bash run_navajo_cron.sh

# Run scheduled La Paz extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/lapaz" && bash run_lapaz_cron.sh

# Run scheduled Santa Cruz extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/SANTA\ CRUZ" && bash run_santacruz_cron.sh

# Run scheduled Coconino extraction NOW (2-day lookback, full OCR)
cd "$PROJECT_ROOT/conino" && bash run_coconino_cron.sh

# ============================================================================
# SECTION 3: RUN ALL 8 COUNTIES WITH CUSTOM SETTINGS
# ============================================================================
# These commands allow fine-grained control over extraction parameters

# Test mode: Extract only 5 documents per county with OCR (quick validation)
for county in graham greenlee cochise gila navajo lapaz santacruz conino; do
  echo "Testing $county with 5 documents..."
  export ${county^^}_OCR_LIMIT=5 2>/dev/null || true
  export ${county^^}_LOOKBACK_DAYS=1
  export ${county^^}_WORKERS=2
done

# Production mode: Extract all documents with full OCR for all counties
for county in graham greenlee cochise gila navajo lapaz santacruz conino; do
  export ${county^^}_OCR_LIMIT=0          # Process ALL documents
  export ${county^^}_LOOKBACK_DAYS=2      # Last 2 days
  export ${county^^}_WORKERS=4            # 4 parallel workers
done

# ============================================================================
# SECTION 4: DATABASE VERIFICATION COMMANDS
# ============================================================================
# Verify that records were successfully inserted into each county table

# Check Graham leads count and recent pipeline runs
python3 << 'PY'
import os
from pathlib import Path
import psycopg

root = Path('.')
for raw in (root/'.env').read_text().splitlines():
    s = raw.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")

url = os.environ.get('DATABASE_URL', '')
with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        print("\n=== GRAHAM COUNTY ===")
        cur.execute('select count(*) from graham_leads')
        print(f"Total leads: {cur.fetchone()[0]}")
        cur.execute("select run_date, total_records, inserted_rows, updated_rows, status from graham_pipeline_runs order by id desc limit 3")
        for r in cur.fetchall():
            print(f"  {r}")
PY

# Check all counties lead counts
for county_table in graham_leads greenlee_leads cochise_leads gila_leads navajo_leads lapaz_leads santacruz_leads coconino_leads; do
  python3 << PY
import os, psycopg
os.environ.update({line.split('=')[0].strip(): line.split('=')[1].strip().strip('"\'') 
                   for line in Path('.env').read_text().splitlines() if line.strip() and '=' in line})
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    with conn.cursor() as cur:
        try:
            cur.execute(f'select count(*) from ${county_table}')
            print(f'{county_table}: {cur.fetchone()[0]} records')
        except: pass
PY
done

# ============================================================================
# SECTION 5: EXTRACTION QUALITY VALIDATION
# ============================================================================
# Verify that OCR and LLM extraction are working properly

# Check OCR quality for recent Graham records
python3 << 'PY'
import os, psycopg, json
from pathlib import Path

# Load environment
for raw in Path('.env').read_text().splitlines():
    if '=' in raw and not raw.startswith('#'):
        k, v = raw.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"\'')

url = os.environ.get('DATABASE_URL')
with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        # Get extraction stats for last run
        cur.execute("""
            SELECT 
              COUNT(*) as total,
              SUM(CASE WHEN ocr_chars > 0 THEN 1 ELSE 0 END) as with_ocr,
              SUM(CASE WHEN used_groq THEN 1 ELSE 0 END) as with_llm,
              SUM(CASE WHEN trustor IS NOT NULL THEN 1 ELSE 0 END) as with_trustor
            FROM graham_leads 
            WHERE run_date = (SELECT MAX(run_date) FROM graham_leads)
        """)
        total, with_ocr, with_llm, with_trustor = cur.fetchone()
        print(f"\n=== OCR/LLM EXTRACTION QUALITY ===")
        print(f"Total documents:      {total}")
        print(f"With OCR text:        {with_ocr} ({with_ocr/total*100:.1f}%)")
        print(f"With Groq LLM:        {with_llm} ({with_llm/total*100:.1f}%)")
        print(f"With trustor field:   {with_trustor} ({with_trustor/total*100:.1f}%)")
        
        if with_llm / total > 0.8:
            print("✓ LLM extraction quality is GOOD")
        elif with_llm / total > 0.5:
            print("⚠ LLM extraction quality is MODERATE - check document accessibility")
        else:
            print("✗ LLM extraction quality is LOW - OCR or document access issues")
PY

# ============================================================================
# SECTION 6: TROUBLESHOOTING
# ============================================================================

# View recent log files
tail -100 "$PROJECT_ROOT/logs/graham_interval.log"
tail -100 "$PROJECT_ROOT/logs/pipeline_$(date +%Y-%m-%d).log"

# Run extraction with verbose output to debug issues
cd "$PROJECT_ROOT/graham" && \
  python3 -c "
from graham.extractor import run_graham_pipeline
result = run_graham_pipeline(
  start_date='3/15/2026',
  end_date='3/18/2026',
  doc_types=['NOTICE', 'FORECLOSURE'],
  max_pages=0,
  ocr_limit=2,  # Process only first 2 for testing
  workers=1,
  use_groq=True,
  headless=True,
  verbose=True,
  write_output_files=False
)
print(f'\nProcessed {len(result.get(\"records\", []))} documents')
for r in result.get('records', [])[:2]:
  print(f'  {r.get(\"documentId\")}: OCR={int(r.get(\"ocrChars\", 0)) > 0}, LLM={r.get(\"usedGroq\", False)}')
"

# ============================================================================
# SECTION 7: CRON JOB SETUP (macOS)
# ============================================================================
# Schedule periodic extraction runs for all counties
# Run every 48 hours at 2 AM UTC

# Edit crontab
crontab -e

# Add these lines to crontab (or similar for your preferred schedule):
# 0 2 * * * cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/graham && bash run_graham_cron.sh >> /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/graham_cron.log 2>&1
# 0 4 * * * cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/greenlee && bash run_greenlee_cron.sh >> /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/greenlee_cron.log 2>&1
# 0 6 * * * cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/cochise && bash run_cochise_cron.sh >> /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/cochise_cron.log 2>&1
# ... (and so on for other counties)

# List current cron jobs
crontab -l | grep -E "graham|greenlee|cochise|gila|navajo|lapaz|santacruz|conino"

echo "✓ All 8 counties are now configured for OCR/LLM extraction and database storage"
