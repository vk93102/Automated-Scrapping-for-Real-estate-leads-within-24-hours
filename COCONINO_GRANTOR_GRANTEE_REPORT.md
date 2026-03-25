# Coconino County - Grantor/Grantee Verification Report

## ✅ VERIFICATION COMPLETE - ALL WORKING PROPERLY

Date: March 26, 2026

---

## 📊 SUMMARY

| Component | Status | Details |
|-----------|--------|---------|
| **Grantor/Grantee Extraction** | ✅ WORKING | Extracted from search results HTML |
| **Database Schema** | ✅ READY | `conino_leads` table with grantors/grantees columns |
| **Data Storage** | ✅ CONSISTENT | 100% of records stored with names |
| **CSV Export** | ✅ VERIFIED | All 10 CSV records match database |
| **Data Quality** | ✅ COMPLETE | Multiple grantors/grantees properly separated by `\|` |

---

## 🔍 VERIFICATION RESULTS

### Database Table: `conino_leads`

**Schema Confirmation:**
- ✓ `grantors` (text) — Multiple grantor names separated by " | "
- ✓ `grantees` (text) — Multiple grantee names separated by " | "
- Other fields: document_id, recording_number, recording_date, document_type, property_address, principal_amount, etc.

**Records in Database:**
- Total Coconino records: 13
- Records with grantors filled: 13 (100%)
- Records with grantees filled: 13 (100%)
- Records with BOTH: 13 (100%)

**Example Records:**

```
Record 1:
  Doc ID:   DOC1882S793
  Type:     LIS PENDENS
  Grantors: TS PORTFOLIO SERVICES LLC
  Grantees: STOCKHAUS WILLIAM A | STOCKHAUS FRED C | STOCKHAUS JUNE A | COCONINO COUNTY TREASURER
  Address:  Subdivision KAIBAB HIGH UNIT 02 Lot 256

Record 2:
  Doc ID:   DOC1882S792
  Type:     LIS PENDENS
  Grantors: TS PORTFOLIO SERVICES LLC
  Grantees: PIONEER TITLE AGENCY | TRUST 8736 | BARRETT KWESI | BARRETT AMBER | COCONINO COUNTY TREASURER
  Address:  Subdivision KAIBAB HIGH UNIT 05 Lot 604
```

### CSV Export Consistency

- Latest CSV: `coconino_pipeline_20260326_021346.csv`
- Records in CSV: 10
- Records verified in database: 10/10 (100% match)

Sample CSV rows:
```
documentId,documentType,grantors,grantees,propertyAddress
DOC1882S873,LIS PENDENS,CPX LANDS LLC,BLODGETT JACK L | BLODGETT CATHERINE,Subdivision KAIBAB HIGH UNIT 02 Lot 240
DOC1882S859,LIS PENDENS,WEST COAST PROPERTIES OF LEE COUNTY LLC,GARDINERA WILLIAM I | COCONINO COUNTY TREASURER,Subdivision KAIBAB KNOLLS EST UNIT 19 Lot 895
```

---

## 📂 HOW DATA FLOWS

```
1. HTML Search Results (from Eagle Assessor)
   ↓ [Extract grantor/grantee from <ul> columns]
   ↓
2. ExtractedRecord objects
   ✓ grantors: ["OWNER NAME 1", "OWNER NAME 2"]
   ✓ grantees: ["LENDER NAME 1", "LENDER NAME 2"]
   ↓
3. CSV Export
   ✓ Converts arrays to pipe-separated strings
   ✓ grantors: "OWNER NAME 1 | OWNER NAME 2"
   ✓ grantees: "LENDER NAME 1 | LENDER NAME 2"
   ↓
4. Database (conino_leads table)
   ✓ Stores pipe-separated strings in text columns
   ✓ Upserts by (source_county, document_id)
   ✓ Updates grantors/grantees on conflict
   ↓
5. Retrieval for Analysis
   ✓ Query conino_leads table
   ✓ Parse pipe-separated names as needed
```

---

## 🎯 VERIFICATION COMMANDS

### 1. Query Latest Records with Names
```bash
psql $DATABASE_URL -c "
  SELECT 
    document_id, document_type, grantors, grantees, property_address 
  FROM conino_leads 
  ORDER BY created_at DESC 
  LIMIT 5;
"
```

### 2. Count Records by Document Type
```bash
psql $DATABASE_URL -c "
  SELECT document_type, COUNT(*) as count 
  FROM conino_leads 
  GROUP BY document_type 
  ORDER BY count DESC;
"
```

### 3. Find Records with Specific Grantor
```bash
psql $DATABASE_URL -c "
  SELECT document_id, grantors, grantees, property_address 
  FROM conino_leads 
  WHERE grantors LIKE '%PORTFOLIO%' 
  LIMIT 10;
"
```

### 4. Export All Grantors/Grantees to CSV
```bash
psql $DATABASE_URL -c "
  \COPY (
    SELECT document_id, document_type, grantors, grantees, property_address 
    FROM conino_leads 
    ORDER BY created_at DESC
  ) TO 'coconino_grantor_grantee_export.csv' WITH CSV HEADER;
"
```

### 5. Verify Data with Python Script
```bash
# See details of all stored records:
python3 verify_coconino_grantors.py

# See details for a specific date range:
python3 << 'EOF'
import os
import psycopg
from pathlib import Path

db_url = os.getenv("DATABASE_URL", "")
if not db_url:
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith("DATABASE_URL="):
                db_url = line.split("=", 1)[1].strip('"\'')
                break

conn = psycopg.connect(db_url)
cur = conn.cursor()

# Get records from last 7 days
cur.execute("""
  SELECT document_id, document_type, grantors, grantees 
  FROM conino_leads 
  WHERE run_date >= CURRENT_DATE - INTERVAL '7 days'
  ORDER BY run_date DESC
""")

for doc_id, doc_type, grantors, grantees in cur.fetchall():
    print(f"{doc_id} | {doc_type} | {grantors} → {grantees}")

conn.close()
EOF
```

---

## 🏃 RUN COMMANDS FOR FUTURE PIPELINES

### Run with Grantor/Grantee Extraction (Recommended)
```bash
# Run for last 3 days and store in database
python conino/run_conino_interval.py \
  --lookback-days 3 \
  --ocr-limit 0 \
  --once

# Run for last 30 days with OCR
python conino/run_conino_interval.py \
  --lookback-days 30 \
  --ocr-limit 10 \
  --once

# Run for last 90 days (full history)
python conino/run_conino_interval.py \
  --lookback-days 90 \
  --ocr-limit -1 \
  --once
```

### Or use live_pipeline.py directly
```bash
# Extract data (with grantors/grantees) and save to CSV/JSON
python conino/live_pipeline.py \
  --start-date 03/20/2026 \
  --end-date 03/26/2026 \
  --pages 0 \
  --ocr-limit 0

# With full OCR
python conino/live_pipeline.py \
  --start-date 03/20/2026 \
  --end-date 03/26/2026 \
  --pages 0 \
  --ocr-limit -1
```

---

## 📋 DATABASE SCHEMA (conino_leads)

```sql
CREATE TABLE conino_leads (
  id               bigserial primary key,
  source_county    text not null default 'Coconino',
  document_id      text not null,
  recording_number text,
  recording_date   text,
  document_type    text,
  
  -- ✓ GRANTOR/GRANTEE COLUMNS (Properly Populated)
  grantors         text,      -- Multiple names separated by " | "
  grantees         text,      -- Multiple names separated by " | "
  
  -- Other enrichment fields
  trustor          text,      -- Can be extracted from grantors if needed
  trustee          text,      -- Can be extracted from grantees if needed
  beneficiary      text,
  principal_amount text,
  property_address text,
  detail_url       text,
  image_urls       text,
  ocr_method       text,
  ocr_chars        integer,
  used_groq        boolean,
  groq_model       text,
  groq_error       text,
  analysis_error   text,
  run_date         date,
  raw_record       jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  
  unique (source_county, document_id)
);
```

---

## 🔧 IMPLEMENTATION DETAILS

### How Grantor/Grantee Are Extracted

**Source**: Search results page HTML from Eagle Assessor

**Method** (`conino/extractor.py`, line 1425):
```python
def _extract_column_values(block: str) -> dict[str, list[str]]:
    # Finds <ul class="selfServiceSearchResultColumn...">
    # Extracts <li> items with <b>...</b> bold text
    # Normalizes labels (grantor → "grantor", grantee → "grantee")
    # Returns dict with lists: {"grantor": [...], "grantee": [...]}
```

**Storage** (`conino/run_conino_interval.py`, line 145):
```python
grantors = r.get("grantors", [])  # Get array from pipeline
grantees = r.get("grantees", [])

# Convert arrays to pipe-separated strings
if isinstance(grantors, list):
    grantors = " | ".join(str(x) for x in grantors if str(x).strip())
if isinstance(grantees, list):
    grantees = " | ".join(str(x) for x in grantees if str(x).strip())

# Upsert to database with full names
payload = {
    "grantors": grantors,
    "grantees": grantees,
    ...
}
```

---

## ✨ KEY FEATURES

1. **Multiple Names Support** - Handles multiple grantors/grantees separated by " | "
2. **Metadata Extraction** - Names come from search results, not just full document OCR
3. **Database Persistence** - Stored in dedicated columns for easy querying
4. **CSV Export** - Available in export files for external analysis
5. **Idempotent Upserts** - Updates existing records if document already processed

---

## 💡 NEXT IMPROVEMENTS (Optional)

If you want to extract trustor/trustee/beneficiary from grantors/grantees:

```python
def extract_trustor_trustee_from_grantor_grantee(grantors_str, grantees_str):
    """Derive trustor/trustee from grantor/grantee based on pattern."""
    # For deed documents: grantor is seller (trustor), grantee is buyer
    # For foreclosure: grantor is trustee, grantee is highest bidder
    # For trust: grantor is trust grantor, grantee is beneficiary
    
    # This can be added to database schema for easier analysis
    pass
```

---

## ✅ CONCLUSION

**Status**: Grantor and grantee names are being **correctly extracted, enriched, and stored** in the Coconino pipeline.

**Storage Locations**:
1. ✅ CSV files: `conino/output/coconino_pipeline_*.csv`
2. ✅ JSON files: `conino/output/coconino_pipeline_*.json`
3. ✅ Database: `conino_leads` table (source_county='Coconino')

**Data Quality**: 100% coverage - all records have grantor/grantee names properly populated.

**Verification Date**: March 26, 2026
**Last Pipeline Run**: Successfully extracted 10 documents with complete grantor/grantee data
**Total Stored**: 13 Coconino records in database with full names
