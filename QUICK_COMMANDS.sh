#!/bin/bash
# QUICK COMMAND REFERENCE - Arizona County Recorder Pipeline
# ============================================================
# All 8 counties with OCR/LLM extraction and database storage
# Last updated: 2026-03-18 23:38:30 UTC

### MONITOR GRAHAM BACKFILL (Currently Running)
tail -f logs/graham_interval.log

### CHECK GRAHAM DATABASE STATUS
python3 << 'PYCHECK'
import os,psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
[os.environ.update({s.split('=')[0].strip():s.split('=')[1].strip()}) for s in Path('.env').read_text().splitlines() if '=' in s and not s.startswith('#')]
c=psycopg.connect(os.environ['DATABASE_URL']).cursor()
c.execute('select count(*),count(CASE WHEN ocr_chars>0 THEN 1 END),count(CASE WHEN used_groq THEN 1 END) from graham_leads')
t,o,l=c.fetchone()
print(f'Graham: {t} total | {o} with OCR ({o/t*100:.1f}%) | {l} with LLM ({l/t*100:.1f}%)')
PYCHECK

### RUN INDIVIDUAL COUNTY BACKFILLS
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours

# Greenlee (30 days)
python3 greenlee/backfill_30days.py

# Cochise (30 days)
python3 cochise/backfill_30days.py

# Gila (30 days)
python3 gila/backfill_30days.py

# Navajo (30 days)
python3 navajo/backfill_30days.py

# La Paz (30 days)
python3 lapaz/backfill_30days.py

# Santa Cruz (30 days)
python3 SANTA\ CRUZ/backfill_30days.py

# Coconino (30 days)
python3 conino/backfill_30days.py

### RUN ALL REMAINING 7 COUNTIES SEQUENTIALLY
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours
for c in greenlee cochise gila navajo lapaz "SANTA CRUZ" conino; do
  [ "$c" = "SANTA CRUZ" ] && python3 "SANTA CRUZ/backfill_30days.py" || python3 "$c/backfill_30days.py"
done

### IMMEDIATE CRON RUNS (2-Day Lookback, Full OCR)
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours

bash graham/run_graham_cron.sh
bash greenlee/run_greenlee_cron.sh
bash cochise/run_cochise_cron.sh
bash gila/run_gila_cron.sh
bash navajo/run_navajo_cron.sh
bash lapaz/run_lapaz_cron.sh
bash SANTA\ CRUZ/run_santacruz_cron.sh
bash conino/run_coconino_cron.sh

### VIEW ALL COUNTY COUNTS
python3 << 'PYALL'
import os,psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
[os.environ.update({s.split('=')[0].strip():s.split('=')[1].strip()}) for s in Path('.env').read_text().splitlines() if '=' in s and not s.startswith('#')]
c=psycopg.connect(os.environ['DATABASE_URL']).cursor()
for t in ['graham_leads','greenlee_leads','cochise_leads','gila_leads','navajo_leads','lapaz_leads','santacruz_leads','coconino_leads']:
  c.execute(f'select count(*) from {t}')
  print(f'{t:<20}: {c.fetchone()[0]}')
PYALL

### DETAILED QUALITY REPORT
python3 << 'PYQUAL'
import os,psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
[os.environ.update({s.split('=')[0].strip():s.split('=')[1].strip()}) for s in Path('.env').read_text().splitlines() if '=' in s and not s.startswith('#')]
c=psycopg.connect(os.environ['DATABASE_URL']).cursor()
print("\nCounty          Total  OCR%   LLM%   Trustor% Status")
print("-"*55)
for t,n in [('graham_leads','Graham'),('greenlee_leads','Greenlee'),('cochise_leads','Cochise'),('gila_leads','Gila'),('navajo_leads','Navajo'),('lapaz_leads','La Paz'),('santacruz_leads','Santa Cruz'),('coconino_leads','Coconino')]:
  try:
    c.execute(f'select count(*),count(CASE WHEN ocr_chars>0 THEN 1 END),count(CASE WHEN used_groq THEN 1 END),count(CASE WHEN trustor IS NOT NULL THEN 1 END) from {t}')
    total,ocr,llm,trustor=c.fetchone()
    if total==0: print(f'{n:<15}0')
    else: print(f'{n:<15}{total:<7}{ocr/total*100:>5.0f}%  {llm/total*100:>5.0f}%  {trustor/total*100:>6.0f}%  {'✓' if llm/total>0.8 else '⚠'}'[:55])
  except: pass
PYQUAL

### SETUP CRON (Edit then add lines below)
crontab -e

# Add these lines:
# 0 12 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/graham && bash run_graham_cron.sh >> logs/graham_cron.log 2>&1
# 0 13 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/greenlee && bash run_greenlee_cron.sh >> logs/greenlee_cron.log 2>&1
# 0 14 * * * [ $(($(date +\%s) / 86400 \% 2)) -eq 0 ] && cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/cochise && bash run_cochise_cron.sh >> logs/cochise_cron.log 2>&1
# ... (add for all 8 counties)

### LIST CURRENT CRON
crontab -l | grep -E "graham|greenlee|cochise|gila|navajo|lapaz|santacruz|conino"

### TROUBLESHOOTING

# Kill stuck extraction
pkill -f "backfill_30days"

# Check if process running
ps aux | grep -E "graham|python3" | grep -v grep

# View last 20 lines of log
tail -20 logs/graham_interval.log

# Get full error detail
tail -100 logs/graham_interval.log | tail -40

# Verify DB connection
python3 << 'PYTEST'
import os,psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
[os.environ.update({s.split('=')[0].strip():s.split('=')[1].strip()}) for s in Path('.env').read_text().splitlines() if '=' in s]
try:
  c=psycopg.connect(os.environ['DATABASE_URL'])
  c.cursor().execute('SELECT 1')
  print('✓ Database connection OK')
except Exception as e:
  print(f'✗ Connection failed: {e}')
PYTEST

### VIEW SAMPLE RECORDS WITH LLM DATA
python3 << 'PYSAM'
import os,psycopg
from pathlib import Path
os.chdir('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
[os.environ.update({s.split('=')[0].strip():s.split('=')[1].strip()}) for s in Path('.env').read_text().splitlines() if '=' in s]
c=psycopg.connect(os.environ['DATABASE_URL']).cursor()
c.execute('SELECT document_id,document_type,trustor,trustee,property_address,ocr_chars,used_groq FROM graham_leads WHERE used_groq=true LIMIT 3')
for doc_id,doc_type,trustor,trustee,address,ocr_chars,used_groq in c.fetchall():
  print(f'{doc_id}: {doc_type}')
  print(f'  Trustor: {trustor or "(empty)"}')
  print(f'  Trustee: {trustee or "(empty)"}')
  print(f'  Address: {address or "(empty)"}')
  print(f'  OCR chars: {ocr_chars}, LLM: {"Yes" if used_groq else "No"}\n')
PYSAM

### KEY FACTS
# - All counties now configured for OCR_LIMIT=0 (full extraction)
# - Document extraction includes: trustor, trustee, property address, principal amount
# - All data stored directly in PostgreSQL (no CSV files)
# - Cron wrappers have lockfiles to prevent overlapping runs
# - Performance: ~1 document per second (30-day = 15-60 minutes per county)
# - Groq API rate limits: ~50-100 requests/minute (watch GROQ_API_KEY)

echo "✓ Command reference ready"
