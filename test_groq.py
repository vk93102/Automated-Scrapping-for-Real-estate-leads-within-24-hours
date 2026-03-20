import psycopg, os, requests, json
from pathlib import Path

[os.environ.__setitem__(k.strip(), v.strip().strip('"').strip("'")) for l in Path('.env').read_text().splitlines() if l.strip() and not l.startswith('#') and '=' in l for k,v in [l.split('=',1)]]

conn = psycopg.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT document_type, ocr_text FROM navajo_leads WHERE groq_error LIKE '%400 Client Error%' LIMIT 1")
row = cur.fetchone()
conn.close()

if not row:
    print("no row")
    exit(0)

print(f"Len text: {len(row[1])}")
messages = [{"role": "user", "content": "hello " + row[1][:100]}]

resp = requests.post(
    "https://api.groq.com/openai/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ.get('GROQ_API_KEY')}"},
    json={"model": "llama-3.3-70b-versatile", "messages": messages}
)
print(resp.status_code, resp.text)
