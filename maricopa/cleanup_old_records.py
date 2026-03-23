#!/usr/bin/env python3
"""
Data Cleanup: Remove records older than 2 weeks
Keeps database fresh and manageable
"""

import os
from pathlib import Path
from datetime import datetime, timedelta
import psycopg2
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment
env_file = Path('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/.env')
for line in env_file.read_text().splitlines():
    if line.strip() and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def cleanup_old_records(days_to_keep: int = 14):
    """
    Remove records older than specified days
    Also cleans up orphaned records
    """
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cursor = conn.cursor()
    
    cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
    
    print(f"\n{'='*100}")
    print(f"🧹 DATA CLEANUP - REMOVING RECORDS OLDER THAN {days_to_keep} DAYS")
    print(f"{'='*100}")
    print(f"Cutoff date: {cutoff_date.isoformat()}")
    
    try:
        # Get counts before cleanup
        cursor.execute("SELECT COUNT(*) as count FROM documents")
        docs_before = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as count FROM properties")
        props_before = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) as count FROM discovered_recordings
        """)
        discovered_before = cursor.fetchone()[0]
        
        print(f"\n📊 BEFORE CLEANUP:")
        print(f"  Documents: {docs_before}")
        print(f"  Properties: {props_before}")
        print(f"  Discovered Recordings: {discovered_before}")
        
        # Count what will be deleted
        cursor.execute("""
            SELECT COUNT(*) as count FROM documents
            WHERE created_at < %s
        """, (cutoff_date,))
        docs_to_delete = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) as count FROM properties p
            JOIN documents d ON p.document_id = d.id
            WHERE d.created_at < %s
        """, (cutoff_date,))
        props_to_delete = cursor.fetchone()[0]
        
        if docs_to_delete == 0:
            print(f"\n✓ No records older than {days_to_keep} days - nothing to delete")
            cursor.close()
            conn.close()
            return
        
        print(f"\n🔍 TO DELETE (older than {days_to_keep} days):")
        print(f"  Documents: {docs_to_delete}")
        print(f"  Properties: {props_to_delete}")
        
        # Confirm deletion
        confirm = input(f"\n⚠️  Confirm deletion of {docs_to_delete} documents and {props_to_delete} properties? (y/N): ")
        
        if confirm.lower() != 'y':
            print("❌ Cleanup cancelled")
            cursor.close()
            conn.close()
            return
        
        # Delete properties (cascades from documents)
        cursor.execute("""
            DELETE FROM properties p
            WHERE p.document_id IN (
                SELECT d.id FROM documents d
                WHERE d.created_at < %s
            )
        """, (cutoff_date,))
        props_deleted = cursor.rowcount
        
        # Delete documents
        cursor.execute("""
            DELETE FROM documents
            WHERE created_at < %s
        """, (cutoff_date,))
        docs_deleted = cursor.rowcount
        
        # Delete old discovered recordings
        cursor.execute("""
            DELETE FROM discovered_recordings
            WHERE created_at < %s
        """, (cutoff_date,))
        discovered_deleted = cursor.rowcount
        
        conn.commit()
        
        # Verify cleanup
        cursor.execute("SELECT COUNT(*) as count FROM documents")
        docs_after = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as count FROM properties")
        props_after = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as count FROM discovered_recordings")
        discovered_after = cursor.fetchone()[0]
        
        print(f"\n✅ CLEANUP COMPLETE:")
        print(f"  Documents deleted: {docs_deleted}")
        print(f"  Properties deleted: {props_deleted}")
        print(f"  Discovered recordings deleted: {discovered_deleted}")
        
        print(f"\n📊 AFTER CLEANUP:")
        print(f"  Documents: {docs_after}")
        print(f"  Properties: {props_after}")
        print(f"  Discovered Recordings: {discovered_after}")
        
        print(f"\n{'='*100}\n")
        
    except Exception as e:
        logger.error(f"Cleanup error: {e}", exc_info=True)
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    cleanup_old_records(days_to_keep=days)
