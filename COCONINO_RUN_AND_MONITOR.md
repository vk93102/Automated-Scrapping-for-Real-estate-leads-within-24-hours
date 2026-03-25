# Coconino County Pipeline: Run & Monitor Guide

**County:** Coconino (Arizona)  
**Data Source:** EagleWeb SelfService @ `eagleassessor.coconino.az.gov:8444`  
**Search Filter:** `DOCSEARCH1213S1` (typed "Coconino Search")  
**Storage:** Supabase Postgres (`public.conino_leads` + `public.conino_pipeline_runs`)

---

## Quick Start Commands

### A) Store 2 weeks of records into Supabase (14-day backfill)
**One-time run, no OCR/LLM enrichment (fast):**
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python run_conino_interval.py \
  --once \
  --lookback-days 14 \
  --ocr-limit -1
```

**With OCR/LLM enrichment on first 5 records:**
```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python run_conino_interval.py \
  --once \
  --lookback-days 14 \
  --ocr-limit 5
```

### B) Show records from Supabase (fetch from DB & display)
**Print summary + last 20 leads:**
```bash
cd "/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours"
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python conino_db_monitor.py \
  --days 14 \
  --runs 10 \
  --doc-types 20 \
  --show-leads 20
```

**Watch mode (refresh every 30 seconds):**
```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python conino_db_monitor.py \
  --days 14 \
  --show-leads 20 \
  --watch \
  --interval 30
```

### C) Continuous monitoring (daemon + UI)
**Terminal 1: Keep the pipeline running (updates DB every ~12 hours):**
```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python run_conino_interval.py \
  --interval-minutes 720 \
  --lookback-days 14 \
  --ocr-limit -1
```

**Terminal 2: Watch live DB stats (refresh every 30s):**
```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python conino_db_monitor.py \
  --days 14 \
  --show-leads 30 \
  --watch --interval 30
```

---

## Document Types Supported (Coconino County)

All Coconino records are filtered to these foreclosure-focused types:

| Document Type | Notes |
|---|---|
| **LIS PENDENS** | Lawsuit filed, pre-sale notice |
| **LIS PENDENS RELEASE** | Lawsuit dismissed/resolved |
| **TRUSTEES DEED UPON SALE** | Deed filed post-trustee foreclosure sale |
| **SHERIFFS DEED** | Deed filed post-sheriff foreclosure sale |
| **NOTICE OF TRUSTEES SALE** | Notice of upcoming trustee sale |
| **TREASURERS DEED** | Deed filed post-tax foreclosure |
| **AMENDED STATE LIEN** | State statutory lien (amended) |
| **STATE LIEN** | State statutory lien |
| **STATE TAX LIEN** | Tax lien filing |
| **RELEASE STATE TAX LIEN** | Tax lien released/cancelled |

*(These are the only types Coconino's server exposes; custom filtering is automatic.)*

---

## File Locations

| Item | Path |
|---|---|
| **Interval Runner** | `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino/run_conino_interval.py` |
| **Live Pipeline** | `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino/live_pipeline.py` |
| **DB Monitor** | `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino/db_monitor.py` |
| **Log File** | `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/conino_interval.log` |
| **Session Cookie** | `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino/output/coconino_cookie.env` |
| **Output (CSV/JSON)** | `/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino/output/` |

---

## Argument Reference

### `run_conino_interval.py` (interval runner / backfill)

```
--once                      Run one cycle and exit (default: loop forever)
--lookback-days N           Fetch records from last N days (default: 7)
--pages N                   Max pages to fetch (0 = all pages, default: 0)
--ocr-limit N               Max records to OCR+LLM:
                            -1 = skip OCR/LLM entirely (fastest)
                             0 = OCR+LLM all records (slowest)
                             N = OCR+LLM first N records
--interval-minutes N        Hours between daemon runs (default: 720 = 12h)
--doc-types TYPE ...        Custom doc types (space-separated)
                            (default: all 10 Coconino-supported types)
--strict-llm                Fail run if not all records got LLM (dev)
--headful                   Show visible browser (manual disambiguation)
```

### `conino_db_monitor.py` (fetch + display records)

```
--days N                    Window size for grouping stats (default: 14)
--runs N                    Show N most recent pipeline runs (default: 10)
--doc-types N               Show top N document types (default: 20)
--show-leads N              Print N most recent lead rows (default: 0 = off)
--watch                     Continuous refresh mode
--interval N                Seconds between refreshes (default: 30)
```

---

## DB Tables

### `public.conino_leads`
Upsert target for all records found.

| Column | Type | Notes |
|---|---|---|
| `document_id` | text | Primary key for upsert |
| `recording_number` | text | Recorder fee number |
| `recording_date` | text | Filing date |
| `document_type` | text | Foreclosure document type |
| `grantors` | text | Comma-separated seller/borrower names |
| `grantees` | text | Comma-separated buyer/trustee names |
| `property_address` | text | Legal description or street address |
| `principal_amount` | text | Loan amount (often "NOT_FOUND") |
| `detail_url` | text | Link to document detail page |
| `image_urls` | text | PDF download links (if found) |
| `run_date` | date | Pipeline run date |
| `updated_at` | timestamp | Last upsert time |
| `created_at` | timestamp | First insertion time |

### `public.conino_pipeline_runs`
Log of all pipeline executions.

| Column | Type | Notes |
|---|---|---|
| `run_date` | date | Date of the run |
| `total_records` | int | Records fetched |
| `inserted_rows` | int | New records inserted |
| `updated_rows` | int | Existing rows updated |
| `llm_used_rows` | int | Records enriched via LLM |
| `status` | text | `success`, `failed`, or `running` |
| `run_started_at` | timestamp | Start time (UTC) |
| `run_finished_at` | timestamp | End time (UTC) |
| `error_message` | text | Exception stack trace (if failed) |

---

## Example Output

```
asof=2026-03-26 02:51:00
conino_leads_total=13 conino_pipeline_runs_total=8

leads_by_run_date:
2026-03-26 count=3
2026-03-25 count=10

recent_runs:
id=8 date=2026-03-26 total=13 ins=3 upd=10 llm=0 status=success started=2026-03-26T02:49:15+00:00 finished=2026-03-26T02:50:43+00:00
id=7 date=2026-03-26 total=10 ins=10 upd=0 llm=0 status=success started=2026-03-25T20:01:38+00:00 finished=2026-03-25T20:05:54+00:00

doc_types_last_window:
   11  LIS PENDENS
    1  TRUSTEES DEED UPON SALE
    1  LIS PENDENS RELEASE

recent_leads:
doc=DOC1882S873 rec=4035994 type=LIS PENDENS run_date=2026-03-26 rec_date=03/24/2026 04:15 PM addr=Subdivision KAIBAB HIGH UNIT 02 Lot 240 url=https://eagleassessor.coconino.az.gov:8444/web/document/DOC1882S873?search=DOCSEARCH1213S1
doc=DOC1882S859 rec=4035981 type=LIS PENDENS run_date=2026-03-26 rec_date=03/24/2026 03:30 PM addr=Subdivision KAIBAB KNOLLS EST UNIT 19 Lot 895 url=https://eagleassessor.coconino.az.gov:8444/web/document/DOC1882S859?search=DOCSEARCH1213S1
...
```

---

## Tips & Troubleshooting

### Issue: "Blocked by Coconino disclaimer reCAPTCHA"
This is normal. The pipeline automatically falls back to Playwright (headless browser) to handle the disclaimer gate.  
If you need visible browser control (manual CAPTCHA):
```bash
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python run_conino_interval.py \
  --once --lookback-days 14 --ocr-limit -1 --headful
```

### Issue: No records found
Possible causes:
- Date range outside available records (usually 30–90 days back)
- Document types not selected (check that `--doc-types` includes valid Coconino types)
- Database connection failed (check `DATABASE_URL` in `.env`)

### Check the log file
```bash
tail -n 100 -f /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/conino_interval.log
```

### Manual SQL queries
```bash
# Count all records
psql "$DATABASE_URL?sslmode=require" -c "select count(*) from public.conino_leads;"

# Show recent runs
psql "$DATABASE_URL?sslmode=require" -c "select id, run_date, total_records, status from public.conino_pipeline_runs order by id desc limit 10;"

# Find records by date
psql "$DATABASE_URL?sslmode=require" -c "select document_id, recording_number, document_type, run_date from public.conino_leads where run_date = '2026-03-26' order by updated_at desc;"
```

---

## Next Steps

1. **First run:** `python run_conino_interval.py --once --lookback-days 14 --ocr-limit -1`
2. **Check results:** `python conino_db_monitor.py --days 14 --show-leads 20`
3. **Set up daemon:** Run the continuous loop in a terminal multiplexer (`tmux`, `screen`) or use `nohup`:
   ```bash
   nohup /Users/vishaljha/.pyenv/versions/3.10.13/bin/python \
     /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/run_conino_interval.py \
     --lookback-days 14 --ocr-limit -1 \
     > /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/logs/conino_daemon.log 2>&1 &
   ```
