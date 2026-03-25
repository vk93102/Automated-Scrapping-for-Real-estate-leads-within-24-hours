#!/usr/bin/env python3
import psycopg

try:
    conn = psycopg.connect(
        "postgresql://vishaljha@127.0.0.1:5432/postgres",
        connect_timeout=12
    )
    
    with conn.cursor() as cur:
        # Check row count
        cur.execute("SELECT COUNT(*) FROM cochise_leads")
        count = cur.fetchone()[0]
        print(f"\n✅ Total records in cochise_leads: {count}")
        
        # Show latest 5 records
        if count > 0:
            print(f"\n📝 Latest 5 records:")
            cur.execute("""
                SELECT document_id, recording_number, recording_date, document_type, 
                       trustor, property_address, used_groq, created_at
                FROM cochise_leads
                ORDER BY created_at DESC
                LIMIT 5
            """)
            for row in cur.fetchall():
                doc_id, rec_num, rec_date, doc_type, trustor, addr, used_groq, created = row
                print(f"    • Doc: {doc_id}, Rec: {rec_num}, Date: {rec_date}, Type: {doc_type}")
                print(f"      Trustor: {trustor}, Address: {addr}, LLM: {used_groq}")
                print()
        
    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")
