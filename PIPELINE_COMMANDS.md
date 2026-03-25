# COCONINO COUNTY PIPELINE - COMMAND-BY-COMMAND EXECUTION GUIDE
# Run these commands end-to-end with progress monitoring

# ============================================================================
# STEP 1: SET UP ENVIRONMENT
# ============================================================================

# Navigate to workspace
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours

# Set Python version
pyenv shell 3.10.13

# Verify Python version
python3 --version

# Check environment variables are set
echo "DATABASE_URL: $DATABASE_URL" | head -c 50
echo ""
echo "GROQ_API_KEY: $GROQ_API_KEY" | head -c 50
echo ""

# ============================================================================
# STEP 2: RUN THE PIPELINE WITH LIVE PROGRESS MONITORING
# ============================================================================

# Option A: Run with full logging (recommended - shows all progress)
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%Y-%m-%d) \
    --end-date $(date +%Y-%m-%d) \
    2>&1 | tee coconino_full_pipeline_$(date +%Y%m%d_%H%M%S).log

# Option B: Run in background and monitor log separately
python3 conino/live_pipeline.py \
    --start-date $(date -v-3d +%Y-%m-%d) \
    --end-date $(date +%Y-%m-%d) \
    > coconino_pipeline_run.log 2>&1 &

# Monitor the log file in another terminal:
tail -f coconino_pipeline_run.log

# ============================================================================
# STEP 3: REAL-TIME PROGRESS MONITORING (Run in separate terminal while pipeline runs)
# ============================================================================

# Watch the log file for each stage
watch 'tail -50 coconino_pipeline_run.log'

# Or monitor specific stages:
# Count documents found
grep "\[SEARCH\]" coconino_pipeline_run.log | tail -5

# Monitor filtering progress
grep "\[FILTER\]" coconino_pipeline_run.log | tail -5

# Monitor detail enrichment
grep "\[DETAIL\]" coconino_pipeline_run.log | tail -5

# Monitor OCR progress in real-time
watch 'grep "OCR" coconino_pipeline_run.log | tail -20'

# ============================================================================
# STEP 4: VERIFY CSV GENERATION
# ============================================================================

# Check if CSV was created
ls -lh conino/output/coconino_pipeline_*.csv | tail -5

# Count records in CSV
wc -l conino/output/coconino_pipeline_*.csv | tail -1

# View CSV headers (column names)
head -1 conino/output/coconino_pipeline_*.csv | tail -1 | tr ',' '\n' | nl

# View first 5 data rows
head -6 conino/output/coconino_pipeline_*.csv | tail -1 | tr ',' '\n'

# ============================================================================
# STEP 5: VERIFY DATABASE STORAGE
# ============================================================================

# Run this Python script to check database
python3 << 'EOF'
import os
import psycopg2
from datetime import datetime

db_url = os.getenv('DATABASE_URL')
if db_url:
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        print("=" * 80)
        print("DATABASE VERIFICATION")
        print("=" * 80)
        
        # Total Coconino records
        cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
        count = cur.fetchone()[0]
        print(f"\n✅ Total COCONINO records in database: {count}")
        
        # Today's records
        today = datetime.now().strftime('%Y-%m-%d')
        cur.execute('''
            SELECT COUNT(*) FROM public.maricopa_properties 
            WHERE county = %s AND recordingDate LIKE %s
        ''', ('COCONINO', f'{today}%'))
        today_count = cur.fetchone()[0]
        print(f"✅ Records added today: {today_count}")
        
        # Sample records
        cur.execute('''
            SELECT documentId, recordingDate, documentType, propertyAddress
            FROM public.maricopa_properties 
            WHERE county = %s
            ORDER BY recordingDate DESC 
            LIMIT 5
        ''', ('COCONINO',))
        
        print("\n📋 5 Most Recent Records:")
        for i, row in enumerate(cur.fetchall(), 1):
            print(f"\n{i}. Document ID: {row[0]}")
            print(f"   Date: {row[1]}")
            print(f"   Type: {row[2]}")
            print(f"   Property: {row[3]}")
        
        cur.close()
        conn.close()
        print("\n" + "=" * 80)
    except Exception as e:
        print(f"❌ Database error: {e}")
else:
    print("❌ DATABASE_URL not set")
EOF

# ============================================================================
# STEP 6: COPY CSV TO FINAL OUTPUT LOCATION
# ============================================================================

# Get the latest CSV file
LATEST_CSV=$(ls -t conino/output/coconino_pipeline_*.csv 2>/dev/null | head -1)

# Copy to main output directory
cp "$LATEST_CSV" "output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"

# Verify the copy
ls -lh "output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"

# Count records in final CSV
wc -l "output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"

# ============================================================================
# STEP 7: GENERATE FINAL SUMMARY REPORT
# ============================================================================

python3 << 'EOF'
import os
import json
from pathlib import Path
from datetime import datetime

print("\n" + "="*80)
print("COCONINO COUNTY PIPELINE - FINAL EXECUTION REPORT")
print("="*80 + "\n")

# Find latest pipeline files
output_dir = Path("conino/output")
csv_files = sorted(output_dir.glob("coconino_pipeline_*.csv"), reverse=True)
json_files = sorted(output_dir.glob("coconino_pipeline_*.json"), reverse=True)

if csv_files:
    latest_csv = csv_files[0]
    with open(latest_csv, 'r') as f:
        lines = f.readlines()
        record_count = len(lines) - 1  # Exclude header
        print(f"📊 Latest CSV File: {latest_csv.name}")
        print(f"   Records: {record_count}")
        print(f"   Size: {latest_csv.stat().st_size / 1024:.1f} KB")
        print(f"   Created: {datetime.fromtimestamp(latest_csv.stat().st_mtime)}")

if json_files:
    latest_json = json_files[0]
    with open(latest_json, 'r') as f:
        data = json.load(f)
        print(f"\n📋 Latest JSON File: {latest_json.name}")
        print(f"   Total Results: {len(data.get('records', []))}")
        if 'summary' in data:
            print(f"   Page: {data['summary'].get('page', 'N/A')}")
            print(f"   Total Pages: {data['summary'].get('pageCount', 'N/A')}")

print("\n" + "="*80)
print("✅ PIPELINE EXECUTION COMPLETE")
print("="*80)
print("\nCSV File Location:")
print(f"  → Output Directory: output/Coconino_County_*_FINAL.csv")
print(f"  → Ready for download and analysis")
print("\nDatabase:")
print(f"  → All records stored in Supabase PostgreSQL")
print(f"  → Table: public.maricopa_properties")
print(f"  → County filter: COCONINO")
print("\nNext Steps:")
print(f"  1. Download the CSV file")
print(f"  2. Import to your CRM system")
print(f"  3. Contact leads for property opportunities")
print(f"  4. Schedule next pipeline run\n")
EOF

# ============================================================================
# STEP 8: CHECK IF PIPELINE IS STILL RUNNING
# ============================================================================

# Check if Python process is still running
ps aux | grep "python3 conino/live_pipeline.py" | grep -v grep

# If still running, wait and check log
if [ $? -eq 0 ]; then
    echo "Pipeline still running... Checking progress..."
    tail -30 coconino_pipeline_run.log
fi

# ============================================================================
# QUICK REFERENCE COMMANDS
# ============================================================================

# View real-time progress (run while pipeline is executing)
# tail -f coconino_pipeline_run.log

# Count OCR documents processed
# grep -c "OCR [0-9]/[0-9]" coconino_pipeline_run.log

# Find errors in log
# grep -i "error\|failed\|❌" coconino_pipeline_run.log

# Extract document types found
# grep "LIS PENDENS\|DEED\|LIEN" coconino_pipeline_run.log | head -20

# View database records with curl (if API available)
# curl -s "https://api.supabase.co/rest/v1/maricopa_properties?county=eq.COCONINO" \
#   -H "Authorization: Bearer $SUPABASE_KEY" | jq '.[] | {documentId, documentType, propertyAddress}'

# ============================================================================
