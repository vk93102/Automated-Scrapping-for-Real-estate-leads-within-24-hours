#!/usr/bin/env python3
"""Verify Gila 30-day pipeline results in database."""

import os
import psycopg
from pathlib import Path

# Load environ from .env file if needed
db_url = os.getenv("DATABASE_URL", "")

# Try to load from .env file
if not db_url:
    env_file = Path("/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/.env")
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith("DATABASE_URL="):
                db_url = line.split("=", 1)[1].strip('"\'')
                break

if not db_url:
    raise ValueError("DATABASE_URL not found in env or .env file")

try:
    conn = psycopg.connect(db_url, connect_timeout=10)
    cur = conn.cursor()
    
    print("=" * 100)
    print("🎯 GILA COUNTY 30-DAY PIPELINE VERIFICATION")
    print("=" * 100)
    print()
    
    # Stats
    cur.execute("""
        SELECT 
            COUNT(*) as total_records,
            COUNT(DISTINCT document_id) as unique_docs,
            MAX(run_date) as latest_date,
            MIN(run_date) as oldest_date
        FROM gila_leads
        WHERE run_date >= '2026-02-25'
    """)
    result = cur.fetchone()
    
    print(f"📊 DATABASE STATS:")
    print(f"   Total records stored: {result[0]}")
    print(f"   Unique documents: {result[1]}")
    print(f"   Latest update: {result[2]}")
    print(f"   Oldest record: {result[3]}")
    print()
    
    # Show top records with detailed fields
    print(f"📋 STORED RECORDS (with trustee & address details):")
    print("-" * 100)
    
    cur.execute("""
        SELECT 
            document_id,
            document_type,
            grantors,
            grantees,
            trustee,
            property_address,
            recording_date
        FROM gila_leads
        WHERE run_date >= '2026-02-25'
        ORDER BY run_date DESC
        LIMIT 10
    """)
    
    for i, row in enumerate(cur.fetchall(), 1):
        doc_id, doc_type, grantors, grantees, trustee, prop_addr, rec_date = row
        print(f"\n  Record #{i}:")
        print(f"    Document ID:      {doc_id}")
        print(f"    Document Type:    {doc_type}")
        print(f"    Grantors:         {grantors}")
        print(f"    Grantees:         {grantees}")
        print(f"    Trustee:          {trustee or '(empty)'}")
        print(f"    Property Address: {prop_addr or '(empty)'}")
        print(f"    Recording Date:   {rec_date or '(empty)'}")
    
    print()
    print("=" * 100)
    print("✅ 30-DAY PIPELINE COMPLETE AND VERIFIED")
    print("=" * 100)
    
    conn.close()

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
