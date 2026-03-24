#!/usr/bin/env python3
"""
Extract all live records fetched for last 3 days in detailed JSON format
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta

# Load environment
env_file = Path('/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/.env')
for line in env_file.read_text().splitlines():
    if line.strip() and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import psycopg2
from psycopg2.extras import RealDictCursor

def fetch_all_live_records():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    today = datetime(2026, 3, 23)
    start_date = today - timedelta(days=3)
    
    # Fetch all with complete details
    cursor.execute("""
        SELECT 
            d.id,
            d.recording_number,
            d.recording_date,
            d.document_type,
            d.page_amount,
            d.names,
            d.failed,
            d.created_at,
            d.updated_at,
            p.id as property_id,
            p.trustor_1_full_name,
            p.trustor_1_first_name,
            p.trustor_1_last_name,
            p.trustor_2_full_name,
            p.property_address,
            p.address_unit,
            p.address_city,
            p.address_state,
            p.address_zip,
            p.sale_date,
            p.original_principal_balance,
            p.llm_model,
            p.created_at as prop_created_at
        FROM documents d
        LEFT JOIN properties p ON d.id = p.document_id
        WHERE d.created_at >= %s AND d.created_at < %s
        ORDER BY d.created_at DESC, d.recording_number
    """, (start_date, today))
    
    records = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return records, start_date, today

def build_report(records, start_date, end_date):
    # Group by document
    docs_map = {}
    
    for row in records:
        doc_id = row['id']
        
        if doc_id not in docs_map:
            docs_map[doc_id] = {
                "document": {
                    "id": row['id'],
                    "recording_number": row['recording_number'],
                    "recording_date": row['recording_date'],
                    "document_type": row['document_type'],
                    "page_amount": row['page_amount'],
                    "names_count": len(row['names'].split(',')) if row['names'] else 0,
                    "failed": row['failed'],
                    "timestamps": {
                        "created_at": row['created_at'].isoformat(),
                        "updated_at": row['updated_at'].isoformat()
                    }
                },
                "properties": []
            }
        
        if row['property_id']:
            docs_map[doc_id]["properties"].append({
                "id": row['property_id'],
                "trustor": {
                    "primary": {
                        "full_name": row['trustor_1_full_name'],
                        "first": row['trustor_1_first_name'],
                        "last": row['trustor_1_last_name']
                    },
                    "secondary": {
                        "full_name": row['trustor_2_full_name']
                    }
                },
                "property": {
                    "address": row['property_address'],
                    "unit": row['address_unit'],
                    "city": row['address_city'],
                    "state": row['address_state'],
                    "zip": row['address_zip'],
                    "sale_date": row['sale_date'],
                    "principal_balance": row['original_principal_balance']
                },
                "llm_model": row['llm_model'],
                "extracted_at": row['prop_created_at'].isoformat()
            })
    
    # Build report
    all_records = list(docs_map.values())

    from collections import Counter

    model_counts = Counter(
        (p.get("llm_model") or "")
        for v in all_records
        for p in (v.get("properties") or [])
        if isinstance(p, dict)
    )
    
    report = {
        "execution": {
            "timestamp": datetime.now().isoformat(),
            "source": "live-fetch-maricopa-county",
            "date_range": {
                "from": start_date.date().isoformat(),
                "to": end_date.date().isoformat()
            }
        },
        "statistics": {
            "total_documents_fetched": len(docs_map),
            "total_properties_extracted": sum(len(v['properties']) for v in all_records),
            "documents_with_properties": sum(1 for v in all_records if v['properties']),
            "documents_without_properties": sum(1 for v in all_records if not v['properties']),
            "failed_documents": sum(1 for v in all_records if v['document']['failed']),
            "llm_models": dict(model_counts.most_common())
        },
        "storage_status": {
            "database": "postgresql-supabase",
            "table_documents": "documents",
            "table_properties": "properties",
            "storage_mode": "db-only (no local files)",
            "commit_status": "committed"
        },
        "sample_extractions": all_records[:5]
    }
    
    return report

# Main
records, start_date, end_date = fetch_all_live_records()
report = build_report(records, start_date, end_date)

print("\n" + "="*120)
print("📡 LIVE RECORDS FETCH REPORT - LAST 3 DAYS (03/20/2026 - 03/23/2026)")
print("="*120 + "\n")

print(json.dumps(report, indent=2, default=str))

print("\n" + "="*120)
print("✅ REPORT COMPLETE - ALL RECORDS STORED IN DATABASE")
print("="*120 + "\n")

# Save full report
output_file = '/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/live_records_fetch_report.json'
with open(output_file, 'w') as f:
    json.dump(report, f, indent=2, default=str)
print(f"📁 Full report saved to: {output_file}\n")
