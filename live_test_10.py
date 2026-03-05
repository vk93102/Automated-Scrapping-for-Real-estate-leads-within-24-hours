"""
Live test: fetch 10 docs with OCR from DB, run Groq LLM extraction on each.
"""
import os, json, time
import psycopg
from maricopa_scraper.llm_extract import extract_fields_llm
from dataclasses import asdict

DB = os.environ["DATABASE_URL"]

with psycopg.connect(DB) as conn:
    rows = conn.execute(
        """
        SELECT recording_number, recording_date, document_type, page_amount, ocr_text
        FROM documents
        WHERE ocr_text IS NOT NULL AND ocr_text <> ''
        ORDER BY recording_date DESC
        LIMIT 10
        """
    ).fetchall()

print(f"\n✅  Fetched {len(rows)} documents with OCR text from DB\n")
print("=" * 70)

results = []
for i, (rec_num, rec_date, doc_type, pages, ocr_text) in enumerate(rows, 1):
    print(f"\n📄 [{i}/10]  Recording : {rec_num}")
    print(f"            Date      : {rec_date}")
    print(f"            Type      : {doc_type}")
    print(f"            Pages     : {pages}")
    print(f"            OCR chars : {len(ocr_text)}")
    print(f"            Preview   : {ocr_text[:150].strip()!r}")
    print(f"   🤖  Calling Groq  llama-3.1-8b-instant ...")

    t0 = time.time()
    try:
        fields = extract_fields_llm(ocr_text, fallback_to_rule_based=False)
        fd = asdict(fields)
        elapsed = time.time() - t0
        print(f"   ✅  Groq answered in {elapsed:.2f}s")
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"   ❌  Groq error ({elapsed:.2f}s): {exc}")
        fd = {k: None for k in [
            "trustor_1_full_name","trustor_1_first_name","trustor_1_last_name",
            "trustor_2_full_name","trustor_2_first_name","trustor_2_last_name",
            "property_address","address_city","address_state","address_zip",
            "address_unit","sale_date","original_principal_balance"
        ]}

    print(f"   ─────────────────────────────────────────────────────────────────")
    print(f"   🏠  Trustor 1    : {fd['trustor_1_full_name']}")
    print(f"   👤  Trustor 2    : {fd['trustor_2_full_name']}")
    print(f"   📍  Address      : {fd['property_address']}")
    print(f"       City/St/Zip  : {fd['address_city']}, {fd['address_state']} {fd['address_zip']}  unit={fd['address_unit']}")
    print(f"   📅  Sale Date    : {fd['sale_date']}")
    print(f"   💰  Loan Balance : {fd['original_principal_balance']}")
    print(f"   ─────────────────────────────────────────────────────────────────")

    results.append({
        "recordingNumber": str(rec_num),
        "recordingDate": str(rec_date),
        "documentType": str(doc_type),
        "pages": pages,
        **fd,
    })
    time.sleep(0.4)

print(f"\n\n{'='*70}")
print("📋  FULL JSON OUTPUT (all 10 docs):\n")
print(json.dumps(results, indent=2))

# also save to file
with open("output/live_llm_10.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\n💾  Saved to output/live_llm_10.json")
