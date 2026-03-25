#!/bin/bash
# === COCONINO GRANTOR/GRANTEE PIPELINE COMMANDS ===
# Copy-paste ready commands to run Coconino with proper grantor/grantee extraction

echo "==== COCONINO PIPELINE COMMANDS - COPY & PASTE READY ===="
echo

# 1. Quick test - last 3 days
echo "[1] Run Coconino for last 3 days (FAST - no OCR):"
echo "python conino/run_conino_interval.py --lookback-days 3 --ocr-limit 0 --once"
echo

# 2. Standard - last 7 days
echo "[2] Run Coconino for last 7 days (STANDARD):"
echo "python conino/run_conino_interval.py --lookback-days 7 --ocr-limit 0 --once"
echo

# 3. Extended - last 30 days
echo "[3] Run Coconino for last 30 days (EXTENDED):"
echo "python conino/run_conino_interval.py --lookback-days 30 --ocr-limit 5 --once"
echo

# 4. Full history - 90 days
echo "[4] Run Coconino for 90 days (FULL HISTORY):"
echo "python conino/run_conino_interval.py --lookback-days 90 --ocr-limit -1 --once"
echo

# 5. With specific dates
echo "[5] Run Coconino for specific date range:"
echo "python conino/live_pipeline.py --start-date 03/20/2026 --end-date 03/26/2026 --pages 0 --ocr-limit 0"
echo

# 6. Verify grantor/grantee storage
echo "[6] Verify grantor/grantee data in database:"
echo "python3 verify_coconino_grantors.py"
echo

# 7. Query latest records from database
echo "[7] Query latest 5 records with grantor/grantee names:"
echo "psql \$DATABASE_URL -c \"SELECT document_id, document_type, grantors, grantees FROM conino_leads ORDER BY created_at DESC LIMIT 5;\""
echo

# 8. Export all records to CSV
echo "[8] Export all grantor/grantee data to CSV:"
echo "psql \$DATABASE_URL -c \"\copy (SELECT document_id, document_type, grantors, grantees, property_address FROM conino_leads ORDER BY created_at DESC) TO 'coconino_grantors_grantees_export.csv' WITH CSV HEADER;\""
echo

# 9. Find records with specific grantor
echo "[9] Search for records with specific grantor name:"
echo "psql \$DATABASE_URL -c \"SELECT document_id, grantors, grantees, property_address FROM conino_leads WHERE grantors LIKE '%TS PORTFOLIO%' LIMIT 10;\""
echo

# 10. Count statistics
echo "[10] Get statistics on records:"
echo "psql \$DATABASE_URL -c \"SELECT document_type, COUNT(*) as count FROM conino_leads GROUP BY document_type ORDER BY count DESC;\""
echo

# 11. Background continuous run (updates every 5 minutes)
echo "[11] Run Coconino continuously in background (checks every 5 min):"
echo "nohup python conino/run_conino_interval.py --lookback-days 7 --ocr-limit 0 > coconino_continuous.log 2>&1 &"
echo

# 12. Kill background process
echo "[12] Stop background Coconino process:"
echo "pkill -f 'python.*conino/run_conino_interval'"
echo

echo "==== QUICK COMMANDS ===="
echo
echo "Run Coconino (3 days):"
echo "  python conino/run_conino_interval.py --lookback-days 3 --ocr-limit 0 --once"
echo
echo "Verify data:"
echo "  python3 verify_coconino_grantors.py"
echo
echo "View latest records:"
echo "  psql \$DATABASE_URL -c \"SELECT document_id, grantors, grantees FROM conino_leads LIMIT 5;\""
