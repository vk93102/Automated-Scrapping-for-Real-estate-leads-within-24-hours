#!/usr/bin/env python3
import os
import psycopg2

db_url = os.getenv('DATABASE_URL')
if db_url:
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Check total Coconino records
        cur.execute('SELECT COUNT(*) FROM public.maricopa_properties WHERE county = %s', ('COCONINO',))
        count = cur.fetchone()[0]
        print(f'✅ Total COCONINO records in DB: {count}')
        
        # Show recent records
        cur.execute('''
            SELECT documentId, recordingDate, documentType, propertyAddress
            FROM public.maricopa_properties 
            WHERE county = %s
            ORDER BY recordingDate DESC 
            LIMIT 3
        ''', ('COCONINO',))
        
        print('\n📋 3 Most Recent Records:')
        for row in cur.fetchall():
            print(f"  • {row[0]} | {row[1]} | {row[2]}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f'❌ Database error: {e}')
else:
    print('❌ DATABASE_URL not set')
