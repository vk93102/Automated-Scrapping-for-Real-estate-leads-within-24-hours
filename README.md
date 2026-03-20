# Maricopa County Recorder — Automated Real Estate Leads Scraper

Discovers every property-transfer document recorded at Maricopa County each day,
fetches per-document metadata via the public API, OCRs each PDF in-memory (nothing
saved to disk), and stores everything in Supabase in real time.

---

## Project Structure

```
automation/
├── maricopa_scraper/       Core package
│   ├── server.py           FastAPI production API server
│   ├── scraper.py          Main scraper pipeline
│   ├── maricopa_api.py     Public API client (discovery + metadata)
│   ├── db_postgres.py      Supabase/Postgres operations
│   ├── pdf_downloader.py   In-memory PDF fetch (no disk write)
│   ├── ocr_pipeline.py     Tesseract OCR
│   └── extract_rules.py    Field extraction (names, address, sale date, principal)
├── scripts/
│   └── db_smoke_check.py   DB health check
├── run_server.sh           Start the API server
├── run_cron.sh             Cron wrapper (runs every 10 min automatically)
├── gunicorn.conf.py        Production server config
├── Procfile                For Railway / Heroku deployment
└── .env                    Secrets (never committed)
```

---

## One-time Setup

```bash
# 1. Create virtual environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Install system dependencies (macOS)
brew install tesseract poppler

# 3. Configure secrets
cp .env.example .env
# Edit .env and set DATABASE_URL to your Supabase connection string
```

---

## Start the Server

```bash
# Development (foreground, auto-reload)
./run_server.sh

# Production (gunicorn, background daemon)
DAEMON=1 PRODUCTION=1 ./run_server.sh

# Restart production server
DAEMON=1 RESTART=1 PRODUCTION=1 ./run_server.sh

# Stop server
kill $(cat logs/server.pid)

# View server logs
tail -f logs/server.log
```

Server:          http://localhost:8080
Interactive docs: http://localhost:8080/docs

---

## API Endpoints

### GET /api/v1/health
Returns DB connectivity status and active job count.

---

### POST /api/v1/llm/extract
Hosted OCR-to-fields extraction endpoint (Groq-backed on the server).

Request body:
```json
{
  "ocr_text": "...raw OCR text...",
  "fallback_to_rule_based": true
}
```

Response includes `fields` with trustor, address, sale date, and principal balance.

To make pipelines use this endpoint instead of direct Groq calls, set:

```bash
GROQ_LLM_ENDPOINT_URL=https://your-server-domain/api/v1/llm/extract
```

---

### POST /api/v1/scrape
Trigger a scrape for any date range. Returns a jobId immediately.

Request body (JSON):

  begin_date    "2026-03-05"   Start date YYYY-MM-DD
  end_date      "2026-03-05"   End date YYYY-MM-DD (defaults to today)
  days          1              Days back from end_date (ignored when begin_date is set)
  document_code "ALL"          ALL = every type; or specific codes: NS, DT, REL D/T
  limit         0              Max docs; 0 = no cap (process everything found)
  pdf_mode      "memory"       memory = OCR without writing PDF to disk
  only_new      false          Skip docs already stored in the DB

Example:
```json
{
  "begin_date":    "2026-03-05",
  "end_date":      "2026-03-05",
  "document_code": "ALL",
  "limit":         0,
  "pdf_mode":      "memory"
}
```

Response (202):
```json
{
  "jobId":         "abc123",
  "status":        "queued",
  "statusUrl":     "/api/v1/jobs/abc123",
  "logUrl":        "/api/v1/jobs/abc123/log",
  "resultsUrl":    "/api/v1/jobs/abc123/results",
  "supabaseTable": "documents"
}
```

---

### GET /api/v1/jobs/{jobId}
Job status: queued | running | done | error

### GET /api/v1/jobs/{jobId}/log
Live log output as plain text. Call repeatedly while status == running.

### GET /api/v1/jobs/{jobId}/results
Download completed results as CSV. Optional ?cities=Scottsdale,Phoenix filter.

---

## Usage — curl examples

### Full day scrape for a specific date

```bash
JOB=$(curl -s -X POST http://localhost:8080/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{"begin_date":"2026-03-05","end_date":"2026-03-05","document_code":"ALL","limit":0,"pdf_mode":"memory"}')
echo $JOB

JOB_ID=$(echo $JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")
```

### Watch live progress

```bash
curl http://localhost:8080/api/v1/jobs/$JOB_ID/log
```

### Check status

```bash
curl http://localhost:8080/api/v1/jobs/$JOB_ID
```

### Download CSV when done

```bash
curl -o results.csv http://localhost:8080/api/v1/jobs/$JOB_ID/results
```

### Last 3 days

```bash
curl -X POST http://localhost:8080/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{"days":3,"document_code":"ALL","limit":0,"pdf_mode":"memory"}'
```

### Today only — skip already-stored records

```bash
curl -X POST http://localhost:8080/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{"document_code":"ALL","limit":0,"pdf_mode":"memory","only_new":true}'
```

---

## Run scraper directly (no server needed)

```bash
source .env && ./.venv/bin/python -m automation.maricopa_scraper.scraper \
  --begin-date 2026-03-05 \
  --end-date   2026-03-05 \
  --document-code ALL \
  --limit 0 \
  --pdf-mode memory \
  --db-url "$DATABASE_URL" \
  --log-level INFO
```

---

## Cron (automatic every 10 minutes)

Fetches 50 new documents per run — covers all 600+ daily documents automatically.

```bash
# View current cron jobs
crontab -l

# Install cron (if not already added)
(crontab -l 2>/dev/null; echo "*/10 * * * * /Users/vishaljha/Desktop/web\ scrapping/automation/run_cron.sh") | crontab -

# Remove cron job
crontab -l | grep -v run_cron.sh | crontab -

# Watch today's cron log live
tail -f logs/cron_$(date +%Y-%m-%d).log
```

---

## DB health check

```bash
source .env && ./.venv/bin/python scripts/db_smoke_check.py
```

---

## Supabase Tables

  documents       One row per recording number — metadata + full OCR text
  properties      Extracted fields: trustor names, address, sale date, principal balance
  scrape_failures Failed records (404 PDFs auto-resolved, never retried)
