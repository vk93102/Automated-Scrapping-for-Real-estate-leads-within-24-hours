#!/usr/bin/env python3
import psycopg

try:
    conn = psycopg.connect(
        "postgresql://vishaljha@127.0.0.1:5432/postgres",
        connect_timeout=12
    )
    
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cochise_leads LIMIT 0")
        col_names = [desc[0] for desc in cur.description]
        print(f"✅ cochise_leads table has {len(col_names)} columns:")
        for i, col in enumerate(col_names, 1):
            print(f"  {i:2}. {col}")
    
    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")
