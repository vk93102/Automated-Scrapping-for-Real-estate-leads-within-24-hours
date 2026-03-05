# Automation — End-to-end OCR → JSON extraction

This folder contains a runnable pipeline to extract structured fields from OCR text of Notice of Trustee Sale documents.

It also contains a production-grade Maricopa County Recorder scraper that can run daily on a VPS:

- Scrape search results (Playwright; handles Cloudflare challenge via stored session)
- Extract recording numbers
- Call public metadata API
- Download PDF
- OCR (Tesseract)
- Rule-based field extraction (baseline)
- Store into PostgreSQL (Supabase) + write local JSON

## 0) Security first

If you committed or shared your API key, rotate it immediately.

Also: the file `proxy_server/Webshare 10 proxies.txt` contains proxy credentials. Treat it as a secret and rotate/replace those credentials if they were ever committed/shared.

## 1) Install dependencies

From the repo root:

- Install Python deps:
  - `./.venv/bin/python -m pip install -U pip`
  - `./.venv/bin/python -m pip install -r requirements.txt`

Additional system deps for OCR on macOS:

- `brew install tesseract poppler`

Playwright browser install (required for the search page):

- `./.venv/bin/python -m playwright install chromium`

This automation uses the official `openai` Python package.

## 2) Configure environment

Create `automation/.env` (or export env vars) with:

- `OPENAI_API_KEY=...` (recommended)
- `OPENAI_MODEL=gpt-4.1` (optional)

For the Maricopa scraper (Postgres):

- `DATABASE_URL=postgresql://...` (Supabase connection string)
- Optional: `PROXY_LIST_PATH=proxy_list.txt`
- Optional: `LOG_LEVEL=INFO`

For backward compatibility, `OPEN_API_KEY` is also accepted.

## 3) Run single-file extraction

Example:

- `./.venv/bin/python -m automation.backend.extract_notice_json \
  --in downloads/NS_2025-10-01_to_2026-01-01/20250569128.txt \
  --out output/notice_20250569128.json \
  --recording-number 20250569128`

## 4) Run batch extraction

Process a directory of OCR `.txt` files:

- `./.venv/bin/python -m automation.backend.run_batch_extract \
  --txt-dir downloads/NS_2025-10-01_to_2026-01-01 \
  --out-jsonl output/notice_extract.jsonl \
  --out-csv output/notice_extract.csv`

Useful flags:
- `--continue-on-error` keeps going on failures
- `--resume` appends to `--out-jsonl` and skips already-processed `recording_number`s
- `--sleep 0.2` adds a delay between requests

## 5) Optional: upsert to Supabase (PostgreSQL)

If you want the final validated JSON rows written to Postgres via Supabase REST:

- Set env vars (recommended) or pass flags:
  - `SUPABASE_URL=...`
  - `SUPABASE_SERVICE_ROLE_KEY=...`
  - `SUPABASE_TABLE=notice_extracts`

Then run:

- `./.venv/bin/python -m automation.backend.run_batch_extract \
  --txt-dir downloads/NS_2025-10-01_to_2026-01-01 \
  --out-jsonl output/notice_extract.jsonl \
  --out-csv output/notice_extract.csv \
  --supabase-url "$SUPABASE_URL" \
  --supabase-key "$SUPABASE_SERVICE_ROLE_KEY" \
  --supabase-table "$SUPABASE_TABLE"`

Outputs:
- JSONL: one JSON object per line
- CSV: flattened table for spreadsheets

## Notes

- The validator normalizes:
  - `sale_date` to ISO `YYYY-MM-DD` when parseable
  - `original_principal_balance` to digits/decimal only
- It can also fill `address_unit`, `city`, `state`, `zip` from `property_address` deterministically (no guessing). Disable with `--no-fill-from-address` for the single-file script.

## Maricopa daily scraper (end-to-end)

Entry point:

- `./.venv/bin/python -m automation.maricopa_scraper.scraper --no-db --headful`

### Why you may see fewer records than the website

The website page you shared is Cloudflare-protected. We **do not** scrape that HTML page.
Instead, we discover recording numbers via the public API:

- `GET https://publicapi.recorder.maricopa.gov/documents/search`

This API can return **more** than “100+” (e.g. 501 for 2026-03-05).

If you still see fewer rows, it’s usually one of these:

- You ran with `DOCUMENT_CODE=NS` (default), which filters to a subset.
- You ran with a positive `LIMIT` (default 100).
- `ONLY_NEW=1` (default) skips previously-seen/DB-existing recording numbers.

Also note: some recording numbers return `404` on the legacy PDF endpoint. In that case OCR/leads cannot be extracted, but we still output/store the recording number + metadata and record a failure for later retry.

### Get ALL recording numbers for a day (recommended first step)

This fetches *all* document types and does not cap the run:

- `DOCUMENT_CODE=ALL LIMIT=0 ONLY_NEW=0 METADATA_ONLY=1 ./run_scraper.sh`

Then (heavier) OCR + extraction:

- `DOCUMENT_CODE=ALL LIMIT=0 ONLY_NEW=0 PDF_MODE=memory ./run_scraper.sh`

### Start server and monitor progress

Start the API server detached:

- `DAEMON=1 RESTART=1 ./run_server.sh`

Watch server logs:

- `tail -n 200 -f logs/server.log`

Trigger a job (returns a `jobId`):

- `curl -sS -X POST "http://127.0.0.1:8080/run?wait=false" -H "content-type: application/json" -d '{"document_code":"ALL","days":1,"limit":0,"only_new":false,"pdf_mode":"memory"}'`

Check job status:

- `curl -sS "http://127.0.0.1:8080/jobs/<jobId>"`
- `curl -sS "http://127.0.0.1:8080/jobs/<jobId>/csv" > output/job.csv`

### Supabase DB storage

To store records in Supabase Postgres, create `automation/.env` and set:

- `DATABASE_URL=postgresql://...`

Then restart the server (`RESTART=1`) and run the scraper normally; it will upsert documents early, store OCR txt path, and mark processing timestamps.

Important: the Maricopa search page returns `403` to plain HTTP due to a Cloudflare challenge. The scraper uses Playwright.

Recommended first run (interactive, to pass the challenge and save cookies):

- `./.venv/bin/python -m automation.maricopa_scraper.scraper --headful --no-db --days 1 --limit 10`

This writes:

- `storage_state.json` (Playwright cookies/session)
- `downloads/documents/<recording>.pdf`
- `downloads/ocr_text/<recording>.txt`
- `output/output.json`

Then a headless run is typically enough for cron:

- `./.venv/bin/python -m automation.maricopa_scraper.scraper --days 1 --limit 100 --db-url "$DATABASE_URL"`

### Proxies (optional)

- Put proxies in `proxy_list.txt` and run with `--use-proxy`.
- For Playwright itself, pass `--playwright-proxy http://host:port`.

## Cron on a VPS

Make the runner executable:

- `chmod +x run_scraper.sh`

If you also want an HTTP endpoint to download the latest CSV:

- `chmod +x run_server.sh`

Crontab example (runs daily at 2am):

- `0 2 * * * /path/to/automation/run_scraper.sh`

If you want to keep a dated CSV archive each run, set `OUT_CSV_DATED=1` in cron:

- `0 2 * * * OUT_CSV_DATED=1 /path/to/automation/run_scraper.sh`

### CSV download endpoint

Run the API server:

- `./run_server.sh`

Run it detached (writes `logs/server.log` + `logs/server.pid`):

- `DAEMON=1 ./run_server.sh`

Stop the detached server:

- `kill "$(cat logs/server.pid)"`

Endpoints:

- `GET /health`
- `GET /cities`
- `GET /csv/latest` (latest CSV)
- `GET /csv/latest?city=Phoenix` (filters by city when `output/new_records_latest.json` exists)
- `GET /csv/latest?cities=Phoenix,Tempe` (multi-city filter)

On-demand trigger:

- `POST /run` (starts a scrape job; recommended to protect with `API_TOKEN`)
- Tip: pass `recording_numbers` to bypass Playwright search (useful if Cloudflare blocks headless search)
- `GET /jobs/<jobId>`
- `GET /jobs/<jobId>/csv` (optional filter: `?cities=Phoenix,Tempe`)

Security:

- Set `API_TOKEN=...` and send header `x-api-token: ...` to `POST /run`.

Logs:

- `logs/scraper.log`

# Automated-Scrapping-for-Real-estate-leads-within-24-hours
