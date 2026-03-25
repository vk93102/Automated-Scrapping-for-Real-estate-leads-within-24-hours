#!/usr/bin/env python3
import psycopg

try:
    conn = psycopg.connect(
        "postgresql://vishaljha@127.0.0.1:5432/postgres",
        connect_timeout=12
    )
    
    with conn.cursor() as cur:
        # Check all available tables
        cur.execute("""
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_catalog = 'postgres'
            ORDER BY table_schema, table_name
        """)
        tables = cur.fetchall()
        print(f"✅ Total tables in database: {len(tables)}")
        
        # Look for cochise or leads tables
        cochise_tables = [t for t in tables if 'cochise' in t[1].lower() or 'leads' in t[1].lower()]
        if cochise_tables:
            print(f"\n📋 Found cochise/leads tables:")
            for schema, name in cochise_tables:
                print(f"    • {schema}.{name}")
                
                # Try to count rows
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {schema}.{name}")
                    count = cur.fetchone()[0]
                    print(f"      → {count} rows")
                except:
                    print(f"      → (unable to query)")
        else:
            print(f"\n❌ No cochise or leads tables found")
            
        # Show all available tables in public schema
        print(f"\n📚 All tables in 'public' schema:")
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        for row in cur.fetchall():
            print(f"    • {row[0]}")
        
    conn.close()
except Exception as e:
    print(f"❌ Error: {e}")
