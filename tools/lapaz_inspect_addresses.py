from __future__ import annotations

from pathlib import Path

import psycopg


def _db_url_with_ssl(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if "sslmode=" in u.lower():
        return u
    return f"{u}{'&' if '?' in u else '?'}sslmode=require"


def main() -> int:
    db_url = Path(".supabase_database_url").read_text(encoding="utf-8").strip()
    if not db_url:
        raise SystemExit("Empty db url")
    db_url = _db_url_with_ssl(db_url)

    q = """
    select document_id,
           recording_number,
           recording_date,
           property_address,
           (property_address is null) as property_address_is_null,
           length(property_address) as property_address_len,
           (raw_record->>'propertyAddress') as raw_propertyAddress,
           length((raw_record->>'propertyAddress')) as raw_propertyAddress_len,
           detail_url,
           groq_error
    from lapaz_leads
    order by updated_at desc nulls last
    limit 10;
    """

    with psycopg.connect(db_url, connect_timeout=12) as conn:
        with conn.cursor() as cur:
            cur.execute(q)
            rows = cur.fetchall() or []

    print(f"rows={len(rows)}")
    for (
        document_id,
        recording_number,
        recording_date,
        property_address,
        property_address_is_null,
        property_address_len,
        raw_property_address,
        raw_property_address_len,
        detail_url,
        groq_error,
    ) in rows:
        print("---")
        print(f"document_id={document_id}")
        print(f"recording_number={recording_number}")
        print(f"recording_date={recording_date}")
        print(f"property_address_is_null={property_address_is_null} len={property_address_len}")
        print(f"property_address_repr={repr(property_address)[:260]}")
        print(f"raw_propertyAddress_len={raw_property_address_len}")
        print(f"raw_record.propertyAddress_repr={repr(raw_property_address)[:260]}")
        print(f"detail_url={str(detail_url or '')[:160]}")
        print(f"groq_error={str(groq_error or '')[:160]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
