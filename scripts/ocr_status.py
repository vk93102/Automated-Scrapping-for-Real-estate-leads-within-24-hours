"""Check OCR storage status in the DB."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
for line in open(env_path):
    k, _, v = line.strip().partition("=")
    if k and not k.startswith("#") and v:
        os.environ.setdefault(k.strip(), v.strip())

import psycopg
conn = psycopg.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM documents WHERE ocr_text IS NOT NULL AND ocr_text != ''")
print(f"Docs WITH ocr_text in DB : {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM documents WHERE ocr_text IS NULL OR ocr_text = ''")
print(f"Docs WITHOUT ocr_text   : {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM documents")
print(f"Total documents          : {cur.fetchone()[0]}")

cur.execute("""
    SELECT document_type, COUNT(*) as total,
           SUM(CASE WHEN ocr_text IS NOT NULL AND ocr_text != '' THEN 1 ELSE 0 END) as has_ocr
    FROM documents GROUP BY document_type ORDER BY has_ocr DESC, total DESC LIMIT 20
""")
print("\n  Type            Total  HasOCR")
print("  " + "-"*35)
for r in cur.fetchall():
    mark = " <-- PDF available" if int(r[2] or 0) > 0 else ""
    print(f"  {str(r[0] or '?'):<15} {r[1]:>5}  {int(r[2] or 0):>6}{mark}")

cur.execute("""
    SELECT recording_number, document_type, length(ocr_text)
    FROM documents WHERE ocr_text IS NOT NULL AND ocr_text != ''
    ORDER BY last_processed_at DESC LIMIT 5
""")
rows = cur.fetchall()
print(f"\n  Latest 5 docs with OCR text in DB:")
for r in rows:
    print(f"    {r[0]}  {str(r[1]):<12}  {r[2]} chars")

if rows:
    rec = rows[0][0]
    cur.execute("SELECT left(ocr_text, 300) FROM documents WHERE recording_number = %s", (rec,))
    print(f"\n  Sample OCR text for {rec}:")
    print("  " + repr(cur.fetchone()[0]))

conn.close()
