import psycopg2
import os

DATABASE_URL = 'postgresql://postgres.leritdoepeqrtvdhdvlo:NLN03zfixwGX1qRv@aws-1-ap-south-1.pooler.supabase.com:5432/postgres'
try:
    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode="require"   # 🔥 REQUIRED for Supabase
    )
    print("Connected successfully")

    cur = conn.cursor()
    cur.execute("SELECT NOW();")
    print(cur.fetchone())

    cur.close()
    conn.close()

except Exception as e:
    print("Connection failed:", e)