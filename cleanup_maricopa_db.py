#!/usr/bin/env python3
"""Clear all Maricopa records from database."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from maricopa.db_postgres import connect
from maricopa.dotenv import load_dotenv_if_present

load_dotenv_if_present(".env")

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("❌ ERROR: DATABASE_URL not set")
    sys.exit(1)

try:
    conn = connect(db_url)
    cursor = conn.cursor()
    
    # Count existing records
    cursor.execute("SELECT COUNT(*) FROM documents")
    count_before = cursor.fetchone()[0]
    print(f"📊 Document records before cleanup: {count_before}")
    
    # Delete all documents (cascades to properties)
    cursor.execute("DELETE FROM documents")
    deleted = cursor.rowcount
    print(f"🗑️  Deleted: {deleted} document records")
    
    # Count after
    cursor.execute("SELECT COUNT(*) FROM documents")
    count_after = cursor.fetchone()[0]
    print(f"✅ Document records after cleanup: {count_after}")
    
    cursor.execute("SELECT COUNT(*) FROM properties")
    prop_count_after = cursor.fetchone()[0]
    print(f"✅ Property records after cleanup: {prop_count_after}")
    
    conn.commit()
    conn.close()
    print("\n✅ Maricopa database cleaned successfully!")
    
except Exception as e:
    print(f"❌ Error: {e}", file=sys.stderr)
    sys.exit(1)
