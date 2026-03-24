#!/usr/bin/env python3
"""Clear addresses containing 'Parcel ID' from lapaz_leads and mark as empty."""

import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def main() -> None:
    _load_env()
    
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL not set in .env!")
        sys.exit(1)
    
    conn_str = _db_url_with_ssl(db_url)
    
    try:
        conn = psycopg.connect(conn_str)
        cur = conn.cursor()
        
        print("=" * 70)
        print(" LA PAZ — CLEAR ADDRESSES WITH PARCEL ID")
        print("=" * 70)
        
        # 1. Find records with "Parcel ID" in address
        print("\n1️⃣ Finding records with 'Parcel ID' in address...")
        cur.execute(
            """
            SELECT id, document_id, property_address
            FROM lapaz_leads
            WHERE property_address IS NOT NULL 
              AND property_address ILIKE '%Parcel ID%'
            ORDER BY id DESC;
            """
        )
        records = cur.fetchall()
        print(f"   Found {len(records)} records with 'Parcel ID' in address")
        
        if len(records) > 0:
            print("\n   Sample records to be cleared:")
            for i, (rec_id, doc_id, addr) in enumerate(records[:5]):
                truncated = addr[:60] + "..." if len(addr) > 60 else addr
                print(f"   - ID {rec_id}: {doc_id} → {truncated}")
            if len(records) > 5:
                print(f"   ... and {len(records) - 5} more")
        
        # 2. Update records: clear address and mark as empty
        print("\n2️⃣ Updating records...")
        cur.execute(
            """
            UPDATE lapaz_leads
            SET property_address = NULL,
                analysis_error = CASE 
                    WHEN analysis_error IS NULL THEN 'Address contains only Parcel ID'
                    WHEN analysis_error NOT LIKE '%Parcel ID%' THEN analysis_error || '; Address contains only Parcel ID'
                    ELSE analysis_error
                END,
                manual_review = TRUE,
                manual_review_reasons = CASE 
                    WHEN manual_review_reasons IS NULL THEN 'Empty address after Parcel ID removal'
                    ELSE manual_review_reasons || '; Empty address after Parcel ID removal'
                END,
                updated_at = NOW()
            WHERE property_address IS NOT NULL 
              AND property_address ILIKE '%Parcel ID%';
            """
        )
        
        updated_count = cur.rowcount
        print(f"   ✓ Updated {updated_count} records")
        
        # 3. Verification
        print("\n3️⃣ Verification...")
        cur.execute(
            """
            SELECT COUNT(*) as remaining
            FROM lapaz_leads
            WHERE property_address IS NOT NULL 
              AND property_address ILIKE '%Parcel ID%';
            """
        )
        remaining = cur.fetchone()[0]
        print(f"   Remaining records with 'Parcel ID': {remaining}")
        
        # Get stats
        cur.execute(
            """
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN property_address IS NULL THEN 1 END) as null_addresses,
                COUNT(CASE WHEN manual_review = TRUE THEN 1 END) as manual_review_marked
            FROM lapaz_leads;
            """
        )
        total, null_addr, manual = cur.fetchone()
        print(f"\n   📊 Database Stats:")
        print(f"      Total records: {total}")
        print(f"      NULL addresses: {null_addr}")
        print(f"      Manual review marked: {manual}")
        
        conn.commit()
        cur.close()
        conn.close()
        
        print("\n" + "=" * 70)
        print(f"✅ COMPLETE: {updated_count} records cleaned and marked")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
