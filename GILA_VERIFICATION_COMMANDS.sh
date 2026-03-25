#!/bin/bash
# === GILA COUNTY - GRANTOR/GRANTEE & DOCUMENT URL VERIFICATION COMMANDS ===
# Copy-paste ready commands to verify data is properly extracted and stored

echo "==== GILA GRANTORS/GRANTEES & DOCUMENT URLs - VERIFICATION COMMANDS ===="
echo

# 1. Quick test - Run pipeline for 7 days
echo "[1] RUN PIPELINE - Fetch last 7 days with grantor/grantee extraction:"
echo "python gila/run_gila_interval.py --lookback-days 7 --ocr-limit -1 --write-files --workers 4 --once"
echo

# 2. Extended - Run for 30 days with detail enrichment
echo "[2] RUN PIPELINE - Fetch last 30 days WITH document URL extraction:"
echo "python gila/run_gila_interval.py --lookback-days 30 --ocr-limit 0 --write-files --workers 4 --once"
echo

# 3. View raw grantor/grantee data
echo "[3] VIEW - All records with grantors/grantees/document URLs (latest 10):"
echo "psql \$DATABASE_URL -c \"SELECT document_id, document_type, grantors, grantees, image_urls as document_url FROM gila_leads ORDER BY created_at DESC LIMIT 10;\""
echo

# 4. Check document URL format
echo "[4] VERIFY - Document URLs are properly formatted:"
echo "psql \$DATABASE_URL -c \"SELECT document_id, image_urls FROM gila_leads WHERE image_urls IS NOT NULL AND image_urls != '' LIMIT 3;\""
echo

# 5. Check grantor/grantee coverage
echo "[5] STATS - Count records with complete grantor/grantee data:"
echo "psql \$DATABASE_URL -c \"SELECT COUNT(*) as total, COUNT(CASE WHEN grantors!='' THEN 1 END) as with_grantors, COUNT(CASE WHEN grantees!='' THEN 1 END) as with_grantees, COUNT(CASE WHEN image_urls!='' THEN 1 END) as with_urls FROM gila_leads;\""
echo

# 6. Search by grantor name
echo "[6] SEARCH - Find records by specific grantor (example: NORRIS):"
echo "psql \$DATABASE_URL -c \"SELECT document_id, grantors, grantees, image_urls FROM gila_leads WHERE grantors LIKE '%NORRIS%';\""
echo

# 7. Export to CSV
echo "[7] EXPORT - Export all grantors/grantees to CSV file:"
echo "psql \$DATABASE_URL -c \"\copy (SELECT document_id, document_type, recording_date, grantors, grantees, property_address, image_urls FROM gila_leads ORDER BY created_at DESC) TO 'gila_grantors_grantees_export.csv' WITH CSV HEADER;\""
echo

# 8. View full record as JSON
echo "[8] JSON EXPORT - Export records as JSON (with full details):"
echo "psql \$DATABASE_URL -c \"SELECT json_agg(row_to_json(t)) FROM (SELECT document_id, document_type, recording_date, grantors, grantees, property_address, detail_url, image_urls, created_at FROM gila_leads WHERE grantors IS NOT NULL ORDER BY created_at DESC LIMIT 5) t\" | python3 -m json.tool"
echo

# 9. Check metadata extraction in CSV output
echo "[9] VERIFY CSV - Check if grantor/grantee are in output CSV files:"
echo "head -3 gila/output/gila_leads_*.csv | tail -1"
echo

# 10. Verify pipeline logs
echo "[10] LOGS - View latest pipeline run logs:"
echo "tail -50 logs/gila_interval.log | grep -E '(checkpoint|fetched|total=|inserted|updated)'"
echo

echo "==== QUICK VERIFICATION CHECKLIST ===="
echo
echo "✓ Run pipeline to fetch data:"
echo "  python gila/run_gila_interval.py --lookback-days 7 --ocr-limit -1 --write-files --workers 4 --once"
echo
echo "✓ Verify grantor/grantee in database:"
echo "  psql \$DATABASE_URL -c \"SELECT document_id, grantors, grantees, image_urls FROM gila_leads LIMIT 3;\""
echo
echo "✓ View CSV exports:"
echo "  ls -lh gila/output/gila_leads_*.csv"
echo "  head -3 gila/output/gila_leads_*.csv | grep -v '^==>' | head -2"
echo
echo "✓ Check database stats:"
echo "  psql \$DATABASE_URL -c \"SELECT COUNT(*) FROM gila_leads; SELECT COUNT(CASE WHEN grantors!='' THEN 1 END) FROM gila_leads;\""
echo
