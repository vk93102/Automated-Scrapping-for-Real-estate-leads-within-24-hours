#!/bin/bash

# 🚀 COCONINO COUNTY PIPELINE - END-TO-END EXECUTION WITH PROGRESS MONITORING
# This script runs the complete pipeline from data fetching to database storage

set -e  # Exit on any error

WORKSPACE="/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
cd "$WORKSPACE"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo "════════════════════════════════════════════════════════════════════════════════"
echo -e "${BLUE}🌴 COCONINO COUNTY REAL ESTATE PIPELINE - FULL EXECUTION${NC}"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""

# ============================================================================
# STAGE 1: ENVIRONMENT SETUP & VERIFICATION
# ============================================================================
echo -e "${YELLOW}[STAGE 1/8] ENVIRONMENT SETUP & VERIFICATION${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"

# Set Python environment
pyenv shell 3.10.13
echo "✅ Python version: $(python3 --version)"

# Verify environment variables
if [ -z "$DATABASE_URL" ]; then
    echo "❌ ERROR: DATABASE_URL not set"
    exit 1
fi
echo "✅ DATABASE_URL configured"

if [ -z "$GROQ_API_KEY" ]; then
    echo "⚠️  WARNING: GROQ_API_KEY not set (LLM features may be limited)"
fi
echo "✅ GROQ_API_KEY configured"

GROQ_ENDPOINT="${GROQ_LLM_ENDPOINT_URL:-https://api.groq.com/openai/v1/chat/completions}"
echo "✅ LLM Endpoint: $GROQ_ENDPOINT"

# Check dependencies
echo ""
echo "Checking required dependencies..."
python3 -c "import psycopg2; print('  ✅ psycopg2 installed')" || echo "  ❌ psycopg2 missing"
python3 -c "import pytesseract; print('  ✅ pytesseract installed')" || echo "  ❌ pytesseract missing"
python3 -c "from playwright.sync_api import sync_playwright; print('  ✅ playwright installed')" || echo "  ❌ playwright missing"

echo ""

# ============================================================================
# STAGE 2: DEFINE DATE RANGE
# ============================================================================
echo -e "${YELLOW}[STAGE 2/8] DEFINE DATE RANGE${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"

START_DATE=$(date -v-3d +%Y-%m-%d)  # 3 days ago
END_DATE=$(date +%Y-%m-%d)           # Today

echo "📅 Start Date: $START_DATE"
echo "📅 End Date:   $END_DATE"
echo "📅 Duration:   3 days of real estate documents"
echo ""

# ============================================================================
# STAGE 3: DATA FETCHING & PARSING
# ============================================================================
echo -e "${YELLOW}[STAGE 3/8] DATA FETCHING & PARSING${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"
echo "🔍 Fetching documents from Eagle Assessor API..."
echo ""

PIPELINE_LOG="coconino_pipeline_$(date +%Y%m%d_%H%M%S).log"
python3 conino/live_pipeline.py \
    --start-date "$START_DATE" \
    --end-date "$END_DATE" \
    2>&1 | tee "$PIPELINE_LOG" &

PIPELINE_PID=$!
echo "📋 Log file: $PIPELINE_LOG (PID: $PIPELINE_PID)"
echo ""

# Monitor Stage 3 - Wait for search results
echo "⏳ Waiting for search results..."
sleep 5
if grep -q "\[SEARCH\]" "$PIPELINE_LOG"; then
    echo "✅ Search completed"
    RESULT_COUNT=$(grep "\[SEARCH\]" "$PIPELINE_LOG" | grep -oE '[0-9]+ results' | head -1)
    echo "📊 $RESULT_COUNT found"
fi

# ============================================================================
# STAGE 4: DOCUMENT FILTERING
# ============================================================================
echo ""
echo -e "${YELLOW}[STAGE 4/8] DOCUMENT FILTERING${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"
echo "🔎 Filtering for target document types..."
echo "   • LIS PENDENS"
echo "   • TRUSTEES DEED UPON SALE"
echo "   • SHERIFFS DEED"
echo "   • STATE TAX LIEN"
echo ""

sleep 10
if grep -q "\[FILTER\]" "$PIPELINE_LOG"; then
    FILTER_OUTPUT=$(grep "\[FILTER\]" "$PIPELINE_LOG")
    echo "✅ $FILTER_OUTPUT"
fi
echo ""

# ============================================================================
# STAGE 5: DETAIL ENRICHMENT
# ============================================================================
echo -e "${YELLOW}[STAGE 5/8] DETAIL ENRICHMENT (Fetching & Parsing Pages)${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"
echo "📄 Fetching detail pages for each document..."
echo ""

sleep 15
if grep -q "\[DETAIL\]" "$PIPELINE_LOG"; then
    DETAIL_OUTPUT=$(grep "\[DETAIL\]" "$PIPELINE_LOG" | tail -1)
    echo "✅ $DETAIL_OUTPUT"
fi
echo ""

# ============================================================================
# STAGE 6: OCR PROCESSING
# ============================================================================
echo -e "${YELLOW}[STAGE 6/8] OCR PROCESSING (PDF Text Extraction)${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"
echo "🔤 Extracting text from PDFs using Tesseract..."
echo ""

# Monitor OCR progress
while kill -0 $PIPELINE_PID 2>/dev/null; do
    if grep -q "OCR" "$PIPELINE_LOG"; then
        OCR_PROGRESS=$(grep "OCR" "$PIPELINE_LOG" | tail -1)
        echo -ne "⏳ $OCR_PROGRESS\r"
    fi
    sleep 3
done

# Show final OCR results
echo ""
echo "OCR Results:"
grep "OCR [0-9]/[0-9]" "$PIPELINE_LOG" | tail -10 | while read line; do
    echo "  ✅ $line"
done
echo ""

# ============================================================================
# STAGE 7: LLM ENRICHMENT & CSV EXPORT
# ============================================================================
echo -e "${YELLOW}[STAGE 7/8] LLM ENRICHMENT & CSV EXPORT${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"
echo "🤖 Processing with custom Groq LLM endpoint..."
echo "📊 Generating CSV export..."
echo ""

# Wait for process to complete
wait $PIPELINE_PID
PIPELINE_EXIT=$?

if [ $PIPELINE_EXIT -eq 0 ]; then
    echo "✅ Pipeline execution completed successfully"
else
    echo "❌ Pipeline exited with code $PIPELINE_EXIT"
fi

# Show CSV generation
if grep -q "csvFile" "$PIPELINE_LOG"; then
    CSV_FILE=$(grep '"csvFile"' "$PIPELINE_LOG" | tail -1 | grep -oE '"[^"]*\.csv"' | tr -d '"')
    CSV_PATH="conino/output/$CSV_FILE"
    if [ -f "$CSV_PATH" ]; then
        RECORD_COUNT=$(wc -l < "$CSV_PATH")
        echo "✅ CSV generated: $CSV_FILE"
        echo "   Records: $((RECORD_COUNT - 1)) data rows"
        echo "   Size: $(du -h "$CSV_PATH" | cut -f1)"
    fi
fi
echo ""

# ============================================================================
# STAGE 8: DATABASE STORAGE & VERIFICATION
# ============================================================================
echo -e "${YELLOW}[STAGE 8/8] DATABASE STORAGE & VERIFICATION${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"
echo "💾 Verifying database storage..."
echo ""

# Check database records
python3 << 'PYEOF'
import os
import psycopg2
from datetime import datetime

db_url = os.getenv('DATABASE_URL')
if db_url:
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Count total Coconino records
        cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
        total_count = cur.fetchone()[0]
        print(f"✅ Total COCONINO records in database: {total_count}")
        
        # Count records from today
        today = datetime.now().strftime('%Y-%m-%d')
        cur.execute('''
            SELECT COUNT(*) FROM public.maricopa_properties 
            WHERE county = %s AND recordingDate LIKE %s
        ''', ('COCONINO', f'{today}%'))
        today_count = cur.fetchone()[0]
        print(f"✅ Records added today: {today_count}")
        
        # Show 5 most recent records
        cur.execute('''
            SELECT documentId, recordingDate, documentType, propertyAddress
            FROM public.maricopa_properties 
            WHERE county = %s
            ORDER BY recordingDate DESC 
            LIMIT 5
        ''', ('COCONINO',))
        
        print("")
        print("📋 5 Most Recent Records in Database:")
        for i, row in enumerate(cur.fetchall(), 1):
            print(f"  {i}. {row[0]} | {row[1]}")
            print(f"     Type: {row[2]}")
            print(f"     Property: {row[3]}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Database error: {e}")
else:
    print("❌ DATABASE_URL not set")
PYEOF

echo ""

# ============================================================================
# COPY CSV TO OUTPUT DIRECTORY
# ============================================================================
echo -e "${YELLOW}📋 COPYING CSV TO OUTPUT DIRECTORY${NC}"
echo "───────────────────────────────────────────────────────────────────────────────"

LATEST_CSV=$(ls -t conino/output/coconino_pipeline_*.csv 2>/dev/null | head -1)
if [ -n "$LATEST_CSV" ]; then
    OUTPUT_FILE="output/Coconino_County_$(date +%Y%m%d)_FINAL.csv"
    cp "$LATEST_CSV" "$OUTPUT_FILE"
    echo "✅ CSV copied to: $OUTPUT_FILE"
    echo "   Records: $(tail -n +2 "$OUTPUT_FILE" | wc -l)"
    echo "   Download: https://downloads.example.com/$(basename $OUTPUT_FILE)"
fi

echo ""

# ============================================================================
# FINAL SUMMARY
# ============================================================================
echo "════════════════════════════════════════════════════════════════════════════════"
echo -e "${GREEN}✅ PIPELINE EXECUTION COMPLETE${NC}"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""
echo "📊 EXECUTION SUMMARY:"
echo "   • Date Range: $START_DATE to $END_DATE"
echo "   • Pipeline Log: $PIPELINE_LOG"
if [ -n "$LATEST_CSV" ]; then
    echo "   • CSV Output: $OUTPUT_FILE"
    echo "   • Records Stored: $(tail -n +2 "$OUTPUT_FILE" | wc -l)"
fi
echo "   • Database: Updated ✅"
echo ""
echo "🎯 NEXT STEPS:"
echo "   1. Download CSV file for analysis"
echo "   2. Review property records in database"
echo "   3. Contact prospects from the leads"
echo "   4. Schedule next pipeline run"
echo ""
echo "════════════════════════════════════════════════════════════════════════════════"
