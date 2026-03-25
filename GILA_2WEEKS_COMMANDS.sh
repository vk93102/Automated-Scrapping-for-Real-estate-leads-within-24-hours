#!/bin/bash
# GILA COUNTY 2-WEEK PIPELINE - COMPLETE COMMAND REFERENCE
# ========================================================
# Store last 2 weeks of data into database and verify
# March 26, 2026

PROJECT_ROOT="/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
cd "$PROJECT_ROOT"

echo "🚀 GILA COUNTY 2-WEEK PIPELINE - COMMAND REFERENCE"
echo "=================================================="
echo ""

# ============================================================
# 1. PIPELINE EXECUTION - Store 2 weeks into database
# ============================================================
echo "📝 COMMAND 1: Run Gila pipeline for last 2 weeks"
echo "=================================================="
echo ""
echo "FULL COMMAND:"
echo "python gila/run_gila_interval.py --lookback-days 14 --once --workers 4 --ocr-limit 0 --realtime 2>&1 | tee gila_2weeks_\$(date +%Y%m%d_%H%M%S).log"
echo ""
echo "PARAMETER BREAKDOWN:"
echo "  --lookback-days 14    : Fetch documents from last 14 days"
echo "  --once                : Run once and exit (don't loop)"
echo "  --workers 4           : Use 4 parallel workers for OCR/LLM processing"
echo "  --ocr-limit 0         : Process all records (0 = no limit)"
echo "  --realtime            : Log every record processed in real-time"
echo "  | tee gila_2weeks_... : Save output to timestamped log file"
echo ""
echo "✅ RUN RESULT (March 26, 02:23 UTC):"
echo "   Total records fetched:  1"
echo "   Records inserted:       0"
echo "   Records updated:        3 (idempotent upsert)"
echo "   LLM processed:          0"
echo "   Output CSV:             gila/output/gila_leads_20260326_022310.csv"
echo "   Output JSON:            gila/output/gila_leads_20260326_022310.json"
echo ""
echo ""

# ============================================================
# 2. VERIFY DATABASE - Query Gila records
# ============================================================
echo "📝 COMMAND 2: Verify records stored in database"
echo "==============================================="
echo ""
echo "SIMPLE BASH QUERY:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT COUNT(*) as total, COUNT(DISTINCT document_id) as unique_docs, MAX(updated_at) FROM gila_leads WHERE run_date >= (CURRENT_DATE - INTERVAL '"'"'2 weeks'"'"');"'
echo ""
echo "OR with Python (full details):"
echo "python verify_gila_db.py"
echo ""
echo ""

# ============================================================
# 3. DETAILED INSPECTION - View actual CSV output
# ============================================================
echo "📝 COMMAND 3: View the exported CSV file"
echo "========================================="
echo ""
echo "cat gila/output/gila_leads_20260326_022310.csv"
echo ""
echo "OUTPUT:"
cat "$PROJECT_ROOT/gila/output/gila_leads_20260326_022310.csv"
echo ""
echo ""

# ============================================================
# 4. DETAILED INSPECTION - View actual JSON output
# ============================================================
echo "📝 COMMAND 4: View the exported JSON file"
echo "=========================================="
echo ""
echo "cat gila/output/gila_leads_20260326_022310.json | python3 -m json.tool"
echo ""
echo "KEY RECORD DETAILS:"
echo "  Document ID:       DOC2352S783"
echo "  Recording Number:  2026-002920"
echo "  Document Type:     Deed In Lieu Of Foreclosure"
echo "  Grantors:          LAW OFFICES OF JASON C. TATMAN"
echo "  Grantees:          SECRETARY OF HOUSING AND URBAN DEVELOPMENT"
echo "  Property Address:  P: 20805415"
echo "  Legal Description: Qtr: NE Sec: 36 Town: 1N Rng: 15E"
echo "  URL:               https://selfservice.gilacountyaz.gov/web/document/DOC2352S783"
echo ""
echo ""

# ============================================================
# 5. ADVANCED - Run with different parameters
# ============================================================
echo "📝 ADDITIONAL COMMAND VARIATIONS:"
echo "================================="
echo ""

echo "A) Run for LAST 7 DAYS (one week):"
echo "python gila/run_gila_interval.py --lookback-days 7 --once --workers 4 --ocr-limit 0"
echo ""

echo "B) Run for LAST 30 DAYS (one month):"
echo "python gila/run_gila_interval.py --lookback-days 30 --once --workers 4 --ocr-limit 0"
echo ""

echo "C) Run with SKIP OCR (faster, no text extraction):"
echo "python gila/run_gila_interval.py --lookback-days 14 --once --workers 4 --ocr-limit -1"
echo ""

echo "D) Run with SPECIFIC DOCUMENT TYPES (foreclosures only):"
echo "python gila/run_gila_interval.py --lookback-days 14 --once --workers 4 --doc-types FORECLOSURE 'NOTICE OF SALE' 'TRUSTEES DEED UPON SALE'"
echo ""

echo "E) Run with STRICT LLM (fail if not all records processed):"
echo "python gila/run_gila_interval.py --lookback-days 14 --once --workers 4 --ocr-limit 0 --strict-llm"
echo ""

echo "F) Run with OUTPUT FILE WRITING ENABLED:"
echo "python gila/run_gila_interval.py --lookback-days 14 --once --workers 4 --ocr-limit 0 --write-files"
echo ""

echo ""

# ============================================================
# 6. DATABASE DIRECT QUERIES
# ============================================================
echo "📝 DATABASE DIRECT QUERIES (using psql):"
echo "========================================"
echo ""

echo "A) COUNT ALL GILA RECORDS:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT COUNT(*) FROM gila_leads;"'
echo ""

echo "B) COUNT RECORDS FROM LAST 2 WEEKS:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT COUNT(*) FROM gila_leads WHERE run_date >= (CURRENT_DATE - INTERVAL '"'"'2 weeks'"'"');"'
echo ""

echo "C) SHOW SUMMARY WITH LLM USAGE:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT COUNT(*), SUM(CASE WHEN used_groq THEN 1 ELSE 0 END) as llm_used FROM gila_leads WHERE run_date >= (CURRENT_DATE - INTERVAL '"'"'2 weeks'"'"');"'
echo ""

echo "D) SHOW ALL COLUMNS FOR LAST RECORD:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT * FROM gila_leads ORDER BY created_at DESC LIMIT 1;"'
echo ""

echo "E) SHOW PIPELINE RUN HISTORY:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT run_date, status, total_records, inserted_rows, updated_rows FROM gila_pipeline_runs ORDER BY run_started_at DESC LIMIT 10;"'
echo ""

echo ""

# ============================================================
# 7. DEBUG & MONITORING
# ============================================================
echo "📝 DEBUG & MONITORING COMMANDS:"
echo "==============================="
echo ""

echo "A) Watch pipeline logs in real-time:"
echo "tail -f logs/gila_interval.log"
echo ""

echo "B) List all Gila output files:"
echo "ls -lah gila/output/gila_leads_*.{csv,json}"
echo ""

echo "C) Count lines in latest CSV:"
echo "wc -l gila/output/gila_leads_20260326_022310.csv"
echo ""

echo "D) Check database connection:"
echo 'psql "\$DATABASE_URL?sslmode=require" -c "SELECT version();"'
echo ""

echo ""

echo "✅ END OF COMMAND REFERENCE"
echo "=================================================="
