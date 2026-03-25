#!/usr/bin/env python3
"""Verify Gila records stored in database for last 2 weeks."""
import os
import psycopg
from datetime import date, timedelta

# Load .env
for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")

db_url = os.environ.get("DATABASE_URL", "").strip()
if "sslmode=" not in db_url.lower():
    db_url = db_url + ("&" if "?" in db_url else "?") + "sslmode=require"

conn = psycopg.connect(db_url)
cur = conn.cursor()

today = date.today()
two_weeks = today - timedelta(days=14)

print("\n" + "=" * 120)
print(f"GILA COUNTY DATABASE VERIFICATION - Last 2 Weeks ({two_weeks} to {today})")
print("=" * 120 + "\n")

# Summary stats
cur.execute(
    """
    SELECT COUNT(*), COUNT(DISTINCT document_id), 
           SUM(CASE WHEN used_groq THEN 1 ELSE 0 END), 
           MAX(updated_at)
    FROM gila_leads 
    WHERE run_date >= %s
    """,
    (two_weeks,),
)
total, unique_docs, llm_used, last_updated = cur.fetchone()
print("📊 SUMMARY:")
print(f"   Total Records:        {total}")
print(f"   Unique Documents:     {unique_docs}")
print(f"   LLM Processed:        {llm_used or 0}")
print(f"   Last Updated:         {last_updated}\n")

# Record details
print("=" * 120)
print("DETAILED RECORDS:")
print("=" * 120 + "\n")

cur.execute(
    """
    SELECT document_id, recording_date, document_type, property_address, 
           principal_amount, used_groq, ocr_method, run_date
    FROM gila_leads 
    WHERE run_date >= %s 
    ORDER BY created_at DESC 
    LIMIT 30
    """,
    (two_weeks,),
)
rows = cur.fetchall()

if rows:
    print(
        f"{'Document ID':<20} {'Date':<12} {'Type':<25} {'Address':<40} {'Amount':<15} {'LLM':<5} {'OCR':<10}"
    )
    print("-" * 130)
    for doc_id, rec_date, doc_type, addr, amount, used_groq, ocr, run_date in rows:
        doc_display = str(doc_id)[:20]
        date_display = str(rec_date)[:12]
        type_display = str(doc_type or "UNKNOWN")[:25]
        addr_display = str(addr or "NOT_FOUND")[:40]
        amt_display = str(amount or "N/A")[:15]
        llm_flag = "✓" if used_groq else "✗"
        ocr_display = str(ocr or "none")[:10]
        print(
            f"{doc_display:<20} {date_display:<12} {type_display:<25} {addr_display:<40} {amt_display:<15} {llm_flag:<5} {ocr_display:<10}"
        )
else:
    print("❌ No records found in the last 2 weeks")

# Pipeline execution history
print("\n" + "=" * 120)
print("PIPELINE EXECUTION HISTORY:")
print("=" * 120 + "\n")

cur.execute(
    """
    SELECT run_date, status, total_records, inserted_rows, updated_rows, llm_used_rows,
           EXTRACT(EPOCH FROM (run_finished_at - run_started_at))::int as duration_secs
    FROM gila_pipeline_runs
    WHERE run_date >= %s
    ORDER BY run_started_at DESC
    LIMIT 30
    """,
    (two_weeks,),
)

runs = cur.fetchall()
if runs:
    print(
        f"{'Date':<12} {'Status':<12} {'Total':<8} {'Inserted':<10} {'Updated':<10} {'LLM':<8} {'Duration (sec)':<15}"
    )
    print("-" * 95)
    for run_date, status, total, ins, upd, llm, duration in runs:
        print(
            f"{str(run_date):<12} {status:<12} {total or 0:<8} {ins or 0:<10} {upd or 0:<10} {llm or 0:<8} {duration or 0:<15}"
        )
else:
    print("❌ No pipeline runs found")

conn.close()
print("\n✅ Verification complete!\n")
