import json, os, re
from pathlib import Path

try:
    import psycopg
except Exception as e:
    print(json.dumps({"ok": False, "error": f"psycopg import failed: {e}"}))
    raise SystemExit(0)

[os.environ.__setitem__(*(l.strip().split("=",1))) for l in open(".env") if not l.startswith("#") and "=" in l]
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    print(json.dumps({"ok": False, "error": "No DATABASE_URL found"}))
    raise SystemExit(0)

since_iso = None

query = """
select
  d.id,
  d.recording_number,
  d.recording_date,
  d.document_type,
  d.created_at,
  d.updated_at,
  p.trustor_1_full_name,
  p.trustor_2_full_name,
  p.property_address,
  p.address_city,
  p.address_state,
  p.address_zip,
  p.sale_date,
  p.original_principal_balance,
  p.llm_model
from documents d
left join properties p on p.document_id = d.id
where (%s::timestamptz is null or d.created_at >= %s::timestamptz)
order by d.created_at desc
limit 50000
"""

try:
    with psycopg.connect(database_url, sslmode="require") as conn:
        with conn.cursor() as cur:
            cur.execute(query, (since_iso, since_iso))
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    print(json.dumps({"ok": True, "rows": len(rows)}, default=str))
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))