#!/usr/bin/env python3
"""Verify grantor/grantee data is properly stored in Coconino records."""

import os
import json
from pathlib import Path

try:
    import psycopg
    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False

def verify_database():
    """Query database to verify grantor/grantee storage."""
    if not HAS_PSYCOPG:
        print("❌ psycopg not installed")
        return
    
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().split("\n"):
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip('"\'')
                    break
    
    if not db_url:
        print("❌ DATABASE_URL not found")
        return
    
    try:
        conn = psycopg.connect(db_url, connect_timeout=10)
        cur = conn.cursor()
        
        # Check schema
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'maricopa_properties' 
            ORDER BY ordinal_position
        """)
        
        print("=" * 100)
        print("DATABASE SCHEMA (maricopa_properties)")
        print("=" * 100)
        for col_name, col_type in cur.fetchall():
            marker = "✓" if col_name in ["grantors", "grantees"] else " "
            print(f"  {marker} {col_name:<30} {col_type}")
        
        # Check Coconino records
        cur.execute("""
            SELECT COUNT(*) FROM public.maricopa_properties WHERE county = 'COCONINO'
        """)
        coconino_count = cur.fetchone()[0]
        
        print("\n" + "=" * 100)
        print(f"COCONINO RECORDS TOTAL: {coconino_count}")
        print("=" * 100)
        
        if coconino_count == 0:
            print("No Coconino records found in database")
            conn.close()
            return
        
        # Check grantor/grantee population
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN grantors IS NOT NULL AND grantors != '' THEN 1 END) as with_grantors,
                COUNT(CASE WHEN grantees IS NOT NULL AND grantees != '' THEN 1 END) as with_grantees
            FROM public.maricopa_properties
            WHERE county = 'COCONINO'
        """)
        
        total, with_grantors, with_grantees = cur.fetchone()
        
        print(f"\n  Total Coconino records:        {total}")
        print(f"  Records WITH grantors:         {with_grantors} ({100*with_grantors/max(1,total):.1f}%)")
        print(f"  Records WITH grantees:         {with_grantees} ({100*with_grantees/max(1,total):.1f}%)")
        
        # Show sample records
        print("\n" + "=" * 100)
        print("SAMPLE RECORDS (Last 5)")
        print("=" * 100)
        
        cur.execute("""
            SELECT 
                document_id,
                document_type,
                grantors,
                grantees,
                property_address,
                created_at
            FROM public.maricopa_properties
            WHERE county = 'COCONINO'
            ORDER BY created_at DESC
            LIMIT 5
        """)
        
        for i, (doc_id, doc_type, grantors, grantees, addr, created) in enumerate(cur.fetchall(), 1):
            print(f"\nRecord {i}:")
            print(f"  Doc ID:      {doc_id}")
            print(f"  Type:        {doc_type}")
            print(f"  Grantors:    {grantors if grantors else '(empty)'}")
            print(f"  Grantees:    {grantees if grantees else '(empty)'}")
            print(f"  Address:     {addr if addr else '(empty)'}")
            print(f"  Created:     {created}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Database error: {e}")
        import traceback
        traceback.print_exc()

def verify_csv_output():
    """Check CSV files to verify grantor/grantee data."""
    output_dir = Path("conino/output")
    csv_files = sorted(output_dir.glob("coconino_pipeline_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not csv_files:
        print("\n❌ No CSV files found in conino/output/")
        return
    
    latest_csv = csv_files[0]
    print("\n" + "=" * 100)
    print(f"LATEST CSV FILE: {latest_csv.name}")
    print("=" * 100)
    
    try:
        import csv
        with open(latest_csv) as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            print(f"\nHeaders: {', '.join(headers)}")
            
            grantor_idx = headers.index("grantors") if "grantors" in headers else -1
            grantee_idx = headers.index("grantees") if "grantees" in headers else -1
            
            if grantor_idx == -1:
                print("❌ 'grantors' column not found in CSV")
            if grantee_idx == -1:
                print("❌ 'grantees' column not found in CSV")
            
            print(f"\nSample records (first 3):")
            for i, row in enumerate(reader, 1):
                if i > 3:
                    break
                print(f"\nRecord {i}:")
                print(f"  Grantors: {row.get('grantors', '(missing)')[:80]}")
                print(f"  Grantees: {row.get('grantees', '(missing)')[:80]}")
    
    except Exception as e:
        print(f"❌ CSV error: {e}")

def verify_json_output():
    """Check JSON files to verify grantor/grantee data."""
    output_dir = Path("conino/output")
    json_files = sorted(output_dir.glob("coconino_pipeline_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not json_files:
        print("\n❌ No JSON files found in conino/output/")
        return
    
    latest_json = json_files[0]
    print("\n" + "=" * 100)
    print(f"LATEST JSON FILE: {latest_json.name}")
    print("=" * 100)
    
    try:
        with open(latest_json) as f:
            data = json.load(f)
        
        records = data.get("records", [])
        print(f"\nTotal records in JSON: {len(records)}")
        
        if records:
            print("\nSample records (first 3):")
            for i, rec in enumerate(records[:3], 1):
                print(f"\nRecord {i}:")
                print(f"  Grantors: {rec.get('grantors', [])}")
                print(f"  Grantees: {rec.get('grantees', [])}")
        
    except Exception as e:
        print(f"❌ JSON error: {e}")

if __name__ == "__main__":
    verify_database()
    verify_csv_output()
    verify_json_output()
    
    print("\n" + "=" * 100)
    print("VERIFICATION COMPLETE")
    print("=" * 100)
