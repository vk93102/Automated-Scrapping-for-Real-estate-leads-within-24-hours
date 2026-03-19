import psycopg, os
[os.environ.__setitem__(*(l.strip().split("=",1))) for l in open(".env") if not l.startswith("#") and "=" in l]
database_url = os.environ.get("DATABASE_URL")
try:
    with psycopg.connect(database_url, sslmode="require") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM maricopa_documents d LEFT JOIN maricopa_properties p ON p.document_id = d.id")
            print("maricopa joined count:", cur.fetchone())
            cur.execute("SELECT count(*) FROM documents d LEFT JOIN properties p ON p.document_id = d.id")
            print("base joined count:", cur.fetchone())
except Exception as e:
    print("Error:", e)