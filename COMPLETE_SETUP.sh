#!/bin/bash
# ============================================================================
# ARIZONA COUNTIES - FULL OCR/LLM EXTRACTION AND DATABASE STORAGE
# ============================================================================
# This document provides complete setup and execution commands for all 8 Arizona
# counties to ensure proper OCR extraction, LLM parsing, and database storage.
#
# Status: Graham 30-day backfill is currently running
# Target: Full OCR/LLM extraction + DB storage for all 8 counties
# ============================================================================

PROJECT_ROOT="/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"

# ============================================================================
# SECTION 1: ACTIVE BACKFILL MONITORING
# ============================================================================

# Check Graham backfill progress (currently running in background)
tail -f "$PROJECT_ROOT/logs/graham_interval.log" 2>/dev/null | grep "extraction quality\|Storing records\|COMPLETE"

# Alternative: Check just the latest 20 lines
tail -20 "$PROJECT_ROOT/logs/graham_interval.log"

# Verify Graham database after backfill completes
python3 << 'PYCHECK'
import os, psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"\'')
conn = psycopg.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('select count(*) from graham_leads')
print(f'Graham leads total: {cur.fetchone()[0]}')
cur.execute("select count(*) from graham_leads where trustor is not null")
print(f'Graham leads with trustor: {cur.fetchone()[0]}')
cur.execute("select count(*) from graham_leads where used_groq = true")
print(f'Graham leads with LLM: {cur.fetchone()[0]}')
PYCHECK

# ============================================================================
# SECTION 2: SCHEDULED 30-DAY BACKFILLS FOR ALL 8 COUNTIES
# ============================================================================
# WARNING: These are long-running operations. Run one per session.

# COUNTY 1: Graham (RUNNING - monitor with tail command above)
# Started: 2026-03-18 23:25:44
# Command: python3 graham/backfill_30days.py
# Expected time: 15-60 minutes
# Status: [Check logs/graham_interval.log]

# COUNTY 2: Greenlee (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 greenlee/backfill_30days.py 2>&1 | tee "logs/greenlee_backfill_$(date +%Y%m%d_%H%M%S).log"

# COUNTY 3: Cochise (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 cochise/backfill_30days.py 2>&1 | tee "logs/cochise_backfill_$(date +%Y%m%d_%H%M%S).log"

# COUNTY 4: Gila (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 gila/backfill_30days.py 2>&1 | tee "logs/gila_backfill_$(date +%Y%m%d_%H%M%S).log"

# COUNTY 5: Navajo (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 navajo/backfill_30days.py 2>&1 | tee "logs/navajo_backfill_$(date +%Y%m%d_%H%M%S).log"

# COUNTY 6: La Paz (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 lapaz/backfill_30days.py 2>&1 | tee "logs/lapaz_backfill_$(date +%Y%m%d_%H%M%S).log"

# COUNTY 7: Santa Cruz (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 SANTA\ CRUZ/backfill_30days.py 2>&1 | tee "logs/santacruz_backfill_$(date +%Y%m%d_%H%M%S).log"

# COUNTY 8: Coconino (READY TO RUN)
# Command:
cd "$PROJECT_ROOT" && python3 conino/backfill_30days.py 2>&1 | tee "logs/conino_backfill_$(date +%Y%m%d_%H%M%S).log"

# ============================================================================
# SECTION 3: RUN ALL COUNTIES SEQUENTIALLY (FULL AUTOMATION)
# ============================================================================
# This will run all 8 county backfills one after another
# WARNING: This will take 2-8 hours depending on document volume
# Estimated: 20-30 minutes per county (Graham already running)

cd "$PROJECT_ROOT"
for county_dir in greenlee cochise gila navajo lapaz "SANTA CRUZ" conino; do
  echo "====== Starting $county_dir backfill ======"
  
  # Convert directory name to Python script name
  county_py="${county_dir,,}"
  county_py="${county_py// /_}"
  
  if [ "$county_dir" = "SANTA CRUZ" ]; then
    python3 "SANTA CRUZ/backfill_30days.py" 2>&1 | tee "logs/santacruz_backfill_$(date +%Y%m%d_%H%M%S).log"
  else
    python3 "$county_dir/backfill_30days.py" 2>&1 | tee "logs/${county_dir}_backfill_$(date +%Y%m%d_%H%M%S).log"
  fi
  
  echo "====== Finished $county_dir backfill ======"
  echo ""
  sleep 5  # Small pause between runs
done

echo "✓ All county backfills complete!"

# ============================================================================
# SECTION 4: SCHEDULED CRON EXECUTION (2-HOUR INTERVALS, 2-DAY LOOKBACK)
# ============================================================================
# After backfill completes, setup scheduled runs for continuous updates

# These commands run the latest extraction for each county every 2 days
# Settings: 2-day lookback, full OCR/LLM extraction, direct DB storage

# Run each county NOW (useful for testing after backfill):
for county in graham greenlee cochise gila navajo lapaz santacruz conino; do
  echo "Running $county cron job..."
  case "$county" in
    graham)    cd "$PROJECT_ROOT/graham" && bash run_graham_cron.sh ;;
    greenlee)  cd "$PROJECT_ROOT/greenlee" && bash run_greenlee_cron.sh ;;
    cochise)   cd "$PROJECT_ROOT/cochise" && bash run_cochise_cron.sh ;;
    gila)      cd "$PROJECT_ROOT/gila" && bash run_gila_cron.sh ;;
    navajo)    cd "$PROJECT_ROOT/navajo" && bash run_navajo_cron.sh ;;
    lapaz)     cd "$PROJECT_ROOT/lapaz" && bash run_lapaz_cron.sh ;;
    santacruz) cd "$PROJECT_ROOT/SANTA CRUZ" && bash run_santacruz_cron.sh ;;
    conino)    cd "$PROJECT_ROOT/conino" && bash run_coconino_cron.sh ;;
  esac
  sleep 2
done

# ============================================================================
# SECTION 5: CRONTAB SETUP FOR MACOS
# ============================================================================
# Add these lines to your crontab for automated scheduled runs

# Edit crontab:
# crontab -e
#
# Then add up to 8 entries (one per county), example schedule:
# Every 2 days at different times to avoid overlap

# Graham: Every 2 days at 12:00 PM UTC
0 12 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/graham && bash run_graham_cron.sh >> logs/graham_cron.log 2>&1

# Greenlee: Every 2 days at 1:00 PM UTC
0 13 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/greenlee && bash run_greenlee_cron.sh >> logs/greenlee_cron.log 2>&1

# Cochise: Every 2 days at 2:00 PM UTC
0 14 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/cochise && bash run_cochise_cron.sh >> logs/cochise_cron.log 2>&1

# Gila: Every 2 days at 3:00 PM UTC
0 15 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/gila && bash run_gila_cron.sh >> logs/gila_cron.log 2>&1

# Navajo: Every 2 days at 4:00 PM UTC
0 16 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/navajo && bash run_navajo_cron.sh >> logs/navajo_cron.log 2>&1

# La Paz: Every 2 days at 5:00 PM UTC
0 17 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/lapaz && bash run_lapaz_cron.sh >> logs/lapaz_cron.log 2>&1

# Santa Cruz: Every 2 days at 6:00 PM UTC
0 18 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/SANTA CRUZ" && bash run_santacruz_cron.sh >> logs/santacruz_cron.log 2>&1

# Coconino: Every 2 days at 7:00 PM UTC
0 19 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino && bash run_coconino_cron.sh >> logs/conino_cron.log 2>&1

# ============================================================================
# SECTION 6: VERIFICATION AFTER COMPLETION
# ============================================================================

# Check total leads per county
python3 << 'PYVERIFY'
import os, psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"\'')

url = os.environ['DATABASE_URL']
conn = psycopg.connect(url)
cur = conn.cursor()

counties = {
    'graham_leads': 'Graham',
    'greenlee_leads': 'Greenlee',
    'cochise_leads': 'Cochise',
    'gila_leads': 'Gila',
    'navajo_leads': 'Navajo',
    'lapaz_leads': 'La Paz',
    'santacruz_leads': 'Santa Cruz',
    'coconino_leads': 'Coconino',
}

print("\n=== DATABASE VERIFICATION ===")
print(f"{'County':<15} {'Total':<8} {'With OCR':<10} {'With LLM':<10}")
print("-" * 50)

for table, name in counties.items():
    try:
        cur.execute(f'select count(*) from {table}')
        total = cur.fetchone()[0]
        
        cur.execute(f'select count(*) from {table} where ocr_chars > 0')
        with_ocr = cur.fetchone()[0]
        
        cur.execute(f'select count(*) from {table} where used_groq = true')
        with_llm = cur.fetchone()[0]
        
        print(f"{name:<15} {total:<8} {with_ocr:<10} {with_llm:<10}")
    except Exception as e:
        print(f"{name:<15} [error: {str(e)[:20]}]")

conn.close()
print("=" * 50)
PYVERIFY

# ============================================================================
# SECTION 7: EXTRACTION QUALITY CHECK
# ============================================================================
# Verify OCR and LLM extraction are working properly

python3 << 'PYQUALITY'
import os, psycopg, json
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"\'')

url = os.environ['DATABASE_URL']
conn = psycopg.connect(url)
cur = conn.cursor()

counties = ['graham', 'greenlee', 'cochise', 'gila', 'navajo', 'lapaz', 'santacruz', 'coconino']

print("\n=== EXTRACTION QUALITY REPORT ===")
print(f"{'County':<15} {'OCR %':<8} {'LLM %':<8} {'Trustor %':<10} {'Status':<20}")
print("-" * 70)

for county in counties:
    table = f'{county}_leads'
    try:
        cur.execute(f'select count(*) from {table}')
        total = cur.fetchone()[0]
        if total == 0:
            print(f"{county:<15} {'0':<8} {'0':<8} {'0':<10} {'No data':<20}")
            continue
        
        cur.execute(f'select count(*) from {table} where ocr_chars > 0')
        ocr_pct = (cur.fetchone()[0] / total) * 100
        
        cur.execute(f'select count(*) from {table} where used_groq = true')
        llm_pct = (cur.fetchone()[0] / total) * 100
        
        cur.execute(f'select count(*) from {table} where trustor is not null and trustor != \'\'')
        trustor_pct = (cur.fetchone()[0] / total) * 100
        
        if llm_pct > 80:
            status = "✓ EXCELLENT"
        elif llm_pct > 50:
            status = "⚠ MODERATE"
        else:
            status = "✗ LOW"
        
        print(f"{county:<15} {ocr_pct:<8.1f} {llm_pct:<8.1f} {trustor_pct:<10.1f} {status:<20}")
    except Exception as e:
        print(f"{county:<15} [error] {str(e)[:30]}")

conn.close()
print("=" * 70)
PYQUALITY

# ============================================================================
# SECTION 8: SAMPLE DATA INSPECTION
# ============================================================================
# View a few recent records with full OCR/LLM data

python3 << 'PYSAMPLE'
import os, psycopg, json
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"\'')

url = os.environ['DATABASE_URL']
conn = psycopg.connect(url)
cur = conn.cursor()

# Sample from Graham
print("\n=== SAMPLE GRAHAM RECORDS WITH LLM EXTRACTION ===")
cur.execute('''
select 
  document_id, document_type, trustor, trustee, property_address, 
  principal_amount, ocr_chars, used_groq
from graham_leads 
where used_groq = true 
order by updated_at desc 
limit 3
''')

records = cur.fetchall()
if records:
    for i, (doc_id, doc_type, trustor, trustee, address, amount, ocr_chars, used_groq) in enumerate(records, 1):
        print(f"\nRecord {i}:")
        print(f"  Document ID: {doc_id}")
        print(f"  Document Type: {doc_type}")
        print(f"  Trustor: {trustor or '(empty)'}")
        print(f"  Trustee: {trustee or '(empty)'}")
        print(f"  Address: {address or '(empty)'}")
        print(f"  Principal Amount: {amount or '(empty)'}")
        print(f"  OCR Characters: {ocr_chars}")
        print(f"  LLM Extraction: {'Yes' if used_groq else 'No'}")
else:
    print("(No records with LLM extraction yet - wait for backfill to complete)")

conn.close()
PYSAMPLE

echo ""
echo "✓ Verification complete!"
