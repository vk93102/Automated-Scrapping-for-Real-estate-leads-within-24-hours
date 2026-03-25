# Gila County Data Quality Fixes - March 26, 2026

## Problem Identified
Gila County was returning records with:
- **Empty trustor/trustee fields** - No borrower/lender data
- **Incomplete addresses** - Only legal descriptions (parcel numbers), no street addresses
- **Wrong document types** - Included LIS PENDENS (lawsuit documents) which have no trustor/trustee/beneficiary

## Root Cause Analysis
1. **LIS PENDENS Documents**: Lawsuit notices with NO trustor/trustee relationship (plaintiff/defendant instead)
2. **Address Extraction**: Property address was being set to legal description (parcel number) instead of street address
3. **Missing Field Population**: Trustor/trustee/beneficiary were initialized but never extracted from deed data

## Changes Made

### 1. gila/extractor.py
**Lines 40-56** - Updated DEFAULT_DOCUMENT_TYPES
```python
# Removed: LIS PENDENS, LIS PENDENS RELEASE, STATE TAX LIEN, RELEASE STATE TAX LIEN
# New focus: Actual deed documents
DEFAULT_DOCUMENT_TYPES = [
    "NOTICE OF DEFAULT",
    "NOTICE OF TRUSTEES SALE",  
    "TRUSTEES DEED UPON SALE",
    "SHERIFFS DEED",
    "TREASURERS DEED",
]
```

**Lines 276-320** - Added two helper functions:
1. `_extract_property_address_from_row()` - Extracts real property addresses
2. `_extract_trustor_trustee_from_deed()` - Derives trustor/trustee from grantors/grantees

**Line 362** - Updated property address extraction to use new helper function

### 2. gila/run_gila_interval.py
**Line 609** - Changed default doc_types
```python
# Before: default=sorted(UNIFIED_FORECLOSURE_DOC_TYPES)
# After:  default=sorted(gila_extractor.DEFAULT_DOCUMENT_TYPES)
```

## Next Steps - Run 30-Day Pipeline

To verify fixes work correctly, run:

```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours && \
python gila/run_gila_interval.py \
  --lookback-days 30 \
  --ocr-limit 5 \
  --write-files \
  --workers 4 \
  --once
```

**Parameters:**
- `--lookback-days 30` - Fetch last 30 days (Feb 25 - Mar 26)
- `--ocr-limit 5` - Process 5 records with OCR for detailed extraction
- `--write-files` - Export CSV/JSON outputs
- `--workers 4` - Parallel processing with 4 threads
- `--once` - Run once and exit (not loop)

**Expected Output:**
- Records with proper deed types (no LIS PENDENS)
- Trustee field populated from grantors
- Better property addresses
- CSV/JSON exports in gila/output/

## Database Storage
After pipeline completes:
1. Records auto-upserted to `gila_leads` table in Supabase
2. Idempotent by documentId (prevent duplicates on re-runs)
3. Verify with:
   ```bash
   psql "$DATABASE_URL?sslmode=require" -c \
     "SELECT COUNT(*),
             MAX(run_date) as latest_date
      FROM gila_leads 
      WHERE run_date >= '2026-02-25';"
   ```

## Document Type Changes Impact

**Removed Types** (why):
- LIS PENDENS → Lawsuit documents, no trustor/trustee
- STATE TAX LIEN → Tax liens, not foreclosures
- NOTICE OF REINSTATEMENT → Lien dismissals, minimal data

**Retained Types** (foreclosure-focused):
- NOTICE OF DEFAULT → Start of foreclosure process
- NOTICE OF TRUSTEES SALE → Auction announcement
- TRUSTEES DEED UPON SALE → Actual foreclosure sale deed
- SHERIFFS DEED → Law enforcement sales
- TREASURERS DEED → Tax foreclosure sales
- DEED IN LIEU → Properties surrendered to avoid foreclosure

## Status
✅ Code changes complete
⏳ Pending: Run 30-day pipeline to verify fixes
⏳ Pending: Verify database population
⏳ Pending: Validate trustor/trustee/address extraction
