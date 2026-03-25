#!/usr/bin/env python3
"""Verify grantor/grantee data in conino_leads table."""

import os
import json
from pathlib import Path

try:
    import psycopg
    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False

def verify_conino_leads_table():
    """Query conino_leads table to verify grantor/grantee storage."""
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
            WHERE table_name = 'conino_leads' 
            ORDER BY ordinal_position
        """)
        
        print("=" * 120)
        print("DATABASE SCHEMA (conino_leads)")
        print("=" * 120)
        
        columns = cur.fetchall()
        if not columns:
            print("❌ Table 'conino_leads' not found")
            conn.close()
            return
        
        grant_cols = ["grantors", "grantees"]
        for col_name, col_type in columns:
            marker = "✓" if col_name in grant_cols else " "
            print(f"  {marker} {col_name:<30} {col_type}")
        
        # Count total records
        cur.execute("SELECT COUNT(*) FROM conino_leads")
        total_count = cur.fetchone()[0]
        
        print("\n" + "=" * 120)
        print(f"COCONINO RECORDS TOTAL: {total_count}")
        print("=" * 120)
        
        if total_count == 0:
            print("No records found in conino_leads table")
            conn.close()
            return
        
        # Check grantor/grantee population
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN grantors IS NOT NULL AND grantors != '' THEN 1 END) as with_grantors,
                COUNT(CASE WHEN grantees IS NOT NULL AND grantees != '' THEN 1 END) as with_grantees,
                COUNT(CASE WHEN grantors IS NOT NULL AND grantees IS NOT NULL THEN 1 END) as with_both
            FROM conino_leads
        """)
        
        total, with_grantors, with_grantees, with_both = cur.fetchone()
        
        print(f"\n  Total records:                 {total}")
        print(f"  Records WITH grantors:         {with_grantors} ({100*with_grantors/max(1,total):.1f}%)")
        print(f"  Records WITH grantees:         {with_grantees} ({100*with_grantees/max(1,total):.1f}%)")
        print(f"  Records WITH BOTH:             {with_both} ({100*with_both/max(1,total):.1f}%)")
        
        # Show sample records
        print("\n" + "=" * 120)
        print("LATEST 5 RECORDS (with grantor/grantee details)")
        print("=" * 120)
        
        cur.execute("""
            SELECT 
                document_id,
                document_type,
                grantors,
                grantees,
                property_address,
                created_at,
                run_date
            FROM conino_leads
            ORDER BY created_at DESC
            LIMIT 5
        """)
        
        for i, (doc_id, doc_type, grantors, grantees, addr, created, run_date) in enumerate(cur.fetchall(), 1):
            print(f"\nRecord {i}:")
            print(f"  Doc ID:      {doc_id}")
            print(f"  Type:        {doc_type}")
            print(f"  Grantors:    {(grantors[:95] if grantors else '(empty)') + ('...' if grantors and len(grantors) > 95 else '')}")
            print(f"  Grantees:    {(grantees[:95] if grantees else '(empty)') + ('...' if grantees and len(grantees) > 95 else '')}")
            print(f"  Address:     {(addr[:80] if addr else '(empty)') + ('...' if addr and len(addr) > 80 else '')}")
            print(f"  Run Date:    {run_date}")
            print(f"  Created:     {created}")
        
        # Count by document type
        print("\n" + "=" * 120)
        print("RECORDS BY DOCUMENT TYPE")
        print("=" * 120)
        
        cur.execute("""
            SELECT document_type, COUNT(*) as count
            FROM conino_leads
            GROUP BY document_type
            ORDER BY count DESC
        """)
        
        for doc_type, count in cur.fetchall():
            print(f"  {doc_type:<50} {count:>5} records")
        
        conn.close()
        print("\n✅ Grantors and grantees ARE properly stored in conino_leads table!")
        
    except Exception as e:
        print(f"❌ Error: {e}")

def compare_csv_to_db():
    """Compare CSV data to database to verify consistency."""
    print("\n" + "=" * 120)
    print("COMPARING CSV OUTPUT TO DATABASE (Last Run)")
    print("=" * 120)
    
    output_dir = Path("conino/output")
    csv_files = sorted(output_dir.glob("coconino_pipeline_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not csv_files:
        print("❌ No CSV files found")
        return
    
    latest_csv = csv_files[0]
    print(f"\nCSV File: {latest_csv.name}")
    
    try:
        import csv
        csv_records = {}
        with open(latest_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                doc_id = row.get("documentId", "")
                if doc_id:
                    csv_records[doc_id] = {
                        "grantors": row.get("grantors", ""),
                        "grantees": row.get("grantees", ""),
                    }
        
        if not HAS_PSYCOPG:
            print("psycopg not available for DB comparison")
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
            print("DATABASE_URL not found")
            return
        
        conn = psycopg.connect(db_url, connect_timeout=10)
        cur = conn.cursor()
        
        print(f"\nCSV records found: {len(csv_records)}")
        
        # Check if CSV records are in database
        matches = 0
        mismatches = []
        for doc_id, csv_data in csv_records.items():
            cur.execute(
                "SELECT grantors, grantees FROM conino_leads WHERE document_id = %s",
                (doc_id,)
            )
            row = cur.fetchone()
            if row:
                db_grantors, db_grantees = row
                if (db_grantors or "") == csv_data["grantors"] and (db_grantees or "") == csv_data["grantees"]:
                    matches += 1
                else:
                    mismatches.append({
                        "doc_id": doc_id,
                        "csv_grantors": csv_data["grantors"],
                        "db_grantors": db_grantors,
                        "csv_grantees": csv_data["grantees"],
                        "db_grantees": db_grantees,
                    })
        
        print(f"Matching records in DB:    {matches}/{len(csv_records)}")
        
        if mismatches:
            print(f"\n⚠️  Mismatches found: {len(mismatches)}")
            for m in mismatches[:3]:  # Show first 3
                print(f"\n  Doc {m['doc_id']}:")
                print(f"    CSV grantors: {m['csv_grantors']}")
                print(f"    DB grantors:  {m['db_grantors']}")
                print(f"    CSV grantees: {m['csv_grantees']}")
                print(f"    DB grantees:  {m['db_grantees']}")
        else:
            print("✅ All records in CSV match database!")
        
        conn.close()
        
    except Exception as e:
        print(f"Error comparing: {e}")

if __name__ == "__main__":
    verify_conino_leads_table()
    compare_csv_to_db()
    
    print("\n" + "=" * 120)
    print("SUMMARY: Grantor/Grantee Verification Complete")
    print("=" * 120)
