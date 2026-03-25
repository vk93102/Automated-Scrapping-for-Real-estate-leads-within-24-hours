# Gila & Coconino County Integration into Frontend Dashboard

**Status:** ✅ Complete end-to-end integration

---

## What's Integrated

### Frontend Changes
- **Gila County:** Changed from "Planned" → "Live" in county selector
- **Coconino County:** Changed from "Planned" → "Live" in county selector
- **API Support:**
  - Gila data fetches from `public.gila_leads` table
  - Coconino data fetches from `public.coconino_leads` table
  - Both support date range filtering (day/week/month/all)

### Files Modified

1. **frontend/app/page.js**
   - Updated COUNTIES array to mark Gila & Coconino as "Live"
   
2. **frontend/app/api/leads/route.js**
   - Added Gila table mapping and SQL query for `gila_leads`
   - Already had Coconino table mapping (verified working)

---

## Complete End-to-End Workflow

### Step 1: Populate Database with 2-Week Records

**For Gila County:**
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
python run_gila_interval.py --once --lookback-days 14 --ocr-limit -1 --write-files --workers 2
```

**For Coconino County:**
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
python run_conino_interval.py --once --lookback-days 14 --ocr-limit -1
```

### Step 2: Verify Data in Supabase

**Gila Monitor:**
```bash
python gila_db_monitor.py --days 14 --show-leads 20
```

**Coconino Monitor:**
```bash
python conino_db_monitor.py --days 14 --show-leads 20
```

**Expected Output (Gila):**
```
asof=2026-03-26 02:51:24
gila_leads_total=2 gila_pipeline_runs_total=15

leads_by_run_date:
2026-03-26 count=2

recent_leads:
doc=DOC2352S783 rec=2026-002920 type=Deed In Lieu Of Foreclosure run_date=2026-03-26
```

**Expected Output (Coconino):**
```
asof=2026-03-26 02:51:24
conino_leads_total=13 conino_pipeline_runs_total=8

leads_by_run_date:
2026-03-26 count=13

recent_leads:
doc=DOC1882S873 rec=4035994 type=LIS PENDENS run_date=2026-03-26
```

### Step 3: Start Frontend Dashboard

```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/frontend"
npm install  # (if not already done)
npm run dev
```

**Access Dashboard:**
```
http://localhost:3000
```

### Step 4: View Gila & Coconino Data in Dashboard

1. Open http://localhost:3000 in your browser
2. Click on **"Gila"** or **"Coconino"** in the county selector
3. Choose your date range filter (Last Day / Last Week / Last Month / End to End)
4. View the records table with:
   - Document ID
   - Recording Number
   - Recording Date
   - Document Type
   - Grantors/Grantees
   - Property Address
   - Principal Amount (if available)

---

## Database Schema (Supabase)

### public.gila_leads
| Column | Type | Description |
|---|---|---|
| `id` | bigserial | Primary key |
| `document_id` | text | Unique document identifier |
| `recording_number` | text | County recording number |
| `recording_date` | text | Filing date |
| `document_type` | text | Foreclosure document type |
| `grantors` | text | Seller/borrower names |
| `grantees` | text | Buyer/trustee names |
| `property_address` | text | Legal description or address |
| `principal_amount` | text | Loan amount |
| `detail_url` | text | Link to document detail page |
| `created_at` | timestamp | Insert time |
| `updated_at` | timestamp | Last update time |
| `run_date` | date | Pipeline run date |

### public.coconino_leads
Same schema as gila_leads

### public.gila_pipeline_runs
| Column | Type |
|---|---|
| `id` | bigserial |
| `run_date` | date |
| `total_records` | int |
| `inserted_rows` | int |
| `updated_rows` | int |
| `llm_used_rows` | int |
| `status` | text |
| `run_started_at` | timestamp |
| `run_finished_at` | timestamp |

---

## API Endpoints Now Available

### Fetch Gila Records
```bash
curl "http://localhost:3000/api/leads?county=gila&range=all&limit=100"
```

### Fetch Coconino Records
```bash
curl "http://localhost:3000/api/leads?county=coconino&range=all&limit=100"
```

### Response Format
```json
{
  "range": "all",
  "total": 13,
  "rows": [
    {
      "id": 1,
      "source_county": "gila",
      "document_id": "DOC2352S783",
      "recording_number": "2026-002920",
      "recording_date": "2026-03-24",
      "document_type": "Deed In Lieu Of Foreclosure",
      "grantors": "LENDER LLC",
      "grantees": "PROPERTY OWNER",
      "property_address": "123 Main St",
      "principal_amount": "250000",
      "detail_url": "https://selfservice.gilacountyaz.gov/web/document/DOC2352S783?...",
      "created_at": "2026-03-26T02:50:03Z",
      "updated_at": "2026-03-26T02:50:03Z"
    },
    ...
  ]
}
```

---

## Date Range Filtering

All counties support these filters in the frontend:

| Filter | Range | Logic |
|---|---|---|
| **Last Day** | 1 day | Records from today only |
| **Last Week** | 7 days | Records from past week (including today) |
| **Last Month** | 30 days | Records from past 30 days |
| **End to End** | All | All records ever scraped |

The API automatically calculates the date cutoff based on the `range` parameter:
- `?range=day` → records from today 00:00 onward
- `?range=week` → records from 6 days ago 00:00 onward
- `?range=month` → records from 29 days ago 00:00 onward
- `?range=all` → all records (no date filter)

---

## Continuous Monitoring Dashboard

To keep your dashboard updated with fresh records, run the interval runners in the background:

### Terminal 1: Gila Daemon (updates every 12 hours)
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
python run_gila_interval.py --lookback-days 14 --ocr-limit -1 --write-files --workers 2
```

### Terminal 2: Coconino Daemon (updates every 12 hours)
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
python run_conino_interval.py --lookback-days 14 --ocr-limit -1
```

### Terminal 3: Frontend Dashboard
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/frontend"
npm run dev
```

### Terminal 4: Monitor DB Stats (optional)
```bash
# Gila stats (refresh every 30s)
python gila_db_monitor.py --days 14 --show-leads 20 --watch --interval 30

# Or Coconino stats
# python conino_db_monitor.py --days 14 --show-leads 20 --watch --interval 30
```

---

## Document Types Displayed

### Gila County (Foreclosure-Focused)
- Deed (various forms)
- Deed of Trust
- Notice of Default
- Notice of Trustee Sale
- Lis Pendens
- Lien (various types)
- Foreclosure
- Deed in Lieu
- Trustee's Deed
- Treasurer's Deed
- Cancellation Notice of Sale

### Coconino County (Supported by EagleWeb)
- LIS PENDENS
- LIS PENDENS RELEASE
- TRUSTEES DEED UPON SALE
- SHERIFFS DEED
- NOTICE OF TRUSTEES SALE
- TREASURERS DEED
- AMENDED STATE LIEN
- STATE LIEN
- STATE TAX LIEN
- RELEASE STATE TAX LIEN

---

## Troubleshooting

### Issue: No records showing for Gila/Coconino in dashboard
**Solution:**
1. Check that the runner has completed at least once:
   ```bash
   python gila_db_monitor.py --days 14 --show-leads 5
   ```
2. If total is 0, run the backfill:
   ```bash
   python run_gila_interval.py --once --lookback-days 14 --ocr-limit -1 --write-files --workers 2
   ```
3. Verify DATABASE_URL is set in frontend `.env`:
   ```bash
   grep DATABASE_URL frontend/.env
   ```

### Issue: API returns empty rows
**Solution:**
1. Check the API directly:
   ```bash
   curl "http://localhost:3000/api/leads?county=gila&range=all"
   ```
2. Check Supabase connectivity:
   ```bash
   psql "$DATABASE_URL?sslmode=require" -c "select count(*) from public.gila_leads;"
   ```

### Issue: Dashboard loads but county selector doesn't work
**Solution:**
1. Check browser console for errors (F12 → Console tab)
2. Ensure `npm run dev` is running (should say "ready on http://localhost:3000")
3. Clear browser cache: Ctrl+Shift+Delete / Cmd+Shift+Delete

---

## Summary

✅ **Gila County**
- 2 records stored in `public.gila_leads`
- API endpoint: `/api/leads?county=gila`
- Frontend status: Live
- Commands: `python run_gila_interval.py ...`

✅ **Coconino County**
- 13 records stored in `public.coconino_leads`
- API endpoint: `/api/leads?county=coconino`
- Frontend status: Live
- Commands: `python run_conino_interval.py ...`

✅ **Frontend Dashboard**
- Both counties visible in county selector
- Date range filtering (day/week/month/all)
- Real-time data fetched from Supabase

Both counties are now **fully integrated** into the frontend dashboard end-to-end! 🎉
