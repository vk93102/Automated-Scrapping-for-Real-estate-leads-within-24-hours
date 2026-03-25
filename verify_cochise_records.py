#!/usr/bin/env python3
import os, psycopg
from datetime import date

try:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        conn = psycopg.connect(db_url, connect_timeout=5)
        with conn.cursor() as cur:
            # Total records
            cur.execute("SELECT COUNT(*) FROM cochise_leads")
            total = cur.fetchone()[0]
            print(f"✅ Total records in cochise_leads: {total}")
            
            # Today's records
            today = date.today().isoformat()
            cur.execute("SELECT COUNT(*) FROM cochise_leads WHERE run_date = %s", (today,))
            today_count = cur.fetchone()[0]
            print(f"✅ Today's records (run_date={today}): {today_count}")
            
            # Show latest records
            print(f"\n📝 Latest 4 records added:")
            cur.execute("""
                SELECT document_id, recording_number, recording_date, document_type, 
                       trustor, property_address, used_groq
                FROM cochise_leads
                ORDER BY created_at DESC
                LIMIT 4
            """)
            for row in cur.fetchall():
                doc_id, rec_num, rec_date, doc_type, trustor, addr, used_groq = row
                print(f"  • {doc_id} | {rec_num} | {rec_date} | {doc_type}")
                if trustor:
                    print(f"    Trustor: {trustor[:40]}")
                if addr:
                    print(f"    Address: {addr[:40]}")
                print(f"    LLM: {used_groq}\n")
        
        conn.close()
    else:
        print("❌ DATABASE_URL not set")
except Exception as e:
    print(f"❌ Error: {e}")
