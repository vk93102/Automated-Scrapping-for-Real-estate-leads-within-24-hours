from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import base64
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .cities_az import ARIZONA_CITIES
from .csv_export import filter_by_cities, render_csv_string
from .llm_extract import extract_fields_llm_direct
from .ocr_pipeline import ocr_pdf_bytes_to_text


app = FastAPI(
    title="Maricopa Recorder Scraper API",
    description="""
## Maricopa County Recorder — Automated Document Scraper

Discovers every recording number in a date range via the public API, fetches
per-document metadata, OCRs each PDF **in-memory** (nothing saved to disk),
and stores everything directly in Supabase.

### Quickstart

**1. Trigger a scrape for a specific date range**
```
POST /api/v1/scrape
{
  "begin_date": "2026-03-05",
  "end_date":   "2026-03-05",
  "document_code": "ALL",
  "limit": 0,
  "pdf_mode": "memory"
}
```

**2. Track live progress**
```
GET /api/v1/jobs/{jobId}
GET /api/v1/jobs/{jobId}/log
```

**3. Download results**
```
GET /api/v1/jobs/{jobId}/results
```

All data is also written to Supabase in real-time as each document is processed.
— Broken documents (no document type returned by the API) are skipped and never stored.
""",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow all origins by default; tighten via CORS_ORIGINS env var in production.
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)


def _default_csv_path() -> Path:
    return Path(os.environ.get("CSV_PATH", "output/new_records_latest.csv"))


def _require_token(x_api_token: Optional[str]) -> None:
    expected = os.environ.get("API_TOKEN", "").strip()
    if not expected:
        # If no token is set, allow (for local dev). Recommended to set on VPS.
        return
    if not x_api_token or x_api_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@dataclass
class Job:
    id: str
    status: str  # queued|running|done|error
    created_at: float
    out_csv: str
    out_json: str
    log_path: str
    cities: list[str]
    error: Optional[str] = None


_jobs_lock = threading.Lock()
_jobs: dict[str, Job] = {}


@dataclass
class ArizonaJob:
    id: str
    status: str  # queued|running|done|error
    created_at: float
    out_csv: str
    out_json: str
    log_path: str
    error: Optional[str] = None


_arizona_jobs_lock = threading.Lock()
_arizona_jobs: dict[str, ArizonaJob] = {}


class RunRequest(BaseModel):
    """Parameters for a scrape run.

    Timeline options (pick one pattern):
      - begin_date + end_date  →  explicit range, e.g. {"begin_date":"2026-03-05","end_date":"2026-03-05"}
      - end_date + days        →  N days ending on end_date
      - days only              →  N days ending today

    Set limit=0 for no cap (all documents in the date range).
    Set document_code="ALL" for every document type (recommended for full coverage).
    """
    cities: list[str] = Field(default_factory=list, description="Filter CSV rows by city (optional)")
    recording_numbers: list[str] = Field(default_factory=list, description="Process specific recording numbers instead of searching")
    begin_date: str = Field(default="", description="Start of date range YYYY-MM-DD (overrides days)")
    end_date: str = Field(default="", description="End of date range YYYY-MM-DD (defaults to today)")
    days: int = Field(default=1, description="Days back from end_date (ignored when begin_date is set)")
    limit: int = Field(default=0, description="Max documents to process; 0 = no limit")
    document_code: str = Field(default="ALL", description="Document type filter: ALL, NS, DT, or comma-separated codes")
    only_new: bool = Field(default=False, description="Skip recording numbers already in the database")
    metadata_only: bool = Field(default=False, description="Only store metadata, skip PDF/OCR")
    pdf_mode: str = Field(default="memory", description="PDF handling: memory=OCR without saving to disk, save=write PDF to disk")
    sleep_s: float = Field(default=1.0, description="Delay between documents in seconds")
    use_proxy: bool = Field(default=False, description="Enable proxy rotation")
    use_search: bool = Field(default=True, description="Use API search to discover recording numbers")


class ArizonaApacheRunRequest(BaseModel):
        """Parameters for Apache County Assessor real-time scraping.

        Pass either:
            - `results_url` from an authenticated sale search page, or
            - `search_id` plus pagination arguments.

        The scraper uses environment credentials:
            - `APACHE_ASSESSOR_USERNAME`
            - `APACHE_ASSESSOR_PASSWORD`
        """

        results_url: str = Field(default="", description="Full saleResults.jsp URL generated by the assessor search flow")
        search_id: str = Field(default="", description="Server-generated search id used by saleResults.jsp")
        search_form: dict[str, str] = Field(default_factory=dict, description="Form fields to POST to saleSearchPOST.jsp for generating a fresh searchId")
        begin_date: str = Field(default="", description="Timeline mode start date YYYY-MM-DD")
        end_date: str = Field(default="", description="Timeline mode end date YYYY-MM-DD")
        chunk_months: int = Field(default=1, description="Timeline mode month chunk size")
        begin_date_field: str = Field(default="fromDate", description="Search form field name for begin date")
        end_date_field: str = Field(default="toDate", description="Search form field name for end date")
        page: int = Field(default=1, description="Starting results page")
        page_size: int = Field(default=100, description="Rows per page")
        sort: str = Field(default="Document Type", description="Sort field used by saleResults.jsp")
        dir: str = Field(default="asc", description="Sort direction: asc or desc")
        max_pages: int = Field(default=1, description="Maximum number of result pages to scrape")
        max_records: int = Field(default=0, description="Maximum number of rows to return; 0 = no cap")
        include_details: bool = Field(default=True, description="Fetch account.jsp detail pages for each result row")
        timeout_s: float = Field(default=30.0, description="HTTP timeout for assessor requests")
        use_proxy: bool = Field(default=False, description="Enable proxy rotation using PROXY_LIST_PATH")


class LlmExtractRequest(BaseModel):
    ocr_text: str = Field(..., min_length=1, description="OCR text to parse")
    fallback_to_rule_based: bool = Field(
        default=True,
        description="If Groq fails, return rule-based extraction instead of empty fields",
    )


class LlmExtractDocumentRequest(BaseModel):
    pdf_base64: str = Field(..., min_length=1, description="Base64-encoded PDF content")
    recording_number: str = Field(default="", description="Optional recording number for observability")
    fallback_to_rule_based: bool = Field(
        default=True,
        description="If Groq fails, return rule-based extraction instead of empty fields",
    )


def _python_bin() -> str:
    # Prefer venv python if present.
    venv = Path(".venv/bin/python")
    if venv.exists():
        return str(venv)
    return "python3"


def _run_scraper_subprocess(job: Job, req: RunRequest) -> None:
    with _jobs_lock:
        _jobs[job.id].status = "running"

    cmd = [
        _python_bin(),
        "-m",
        "automation.maricopa_scraper.scraper",
        "--document-code",
        req.document_code,
    ]

    if (req.begin_date or "").strip():
        cmd.extend(["--begin-date", str(req.begin_date).strip()])
    if (req.end_date or "").strip():
        cmd.extend(["--end-date", str(req.end_date).strip()])

    cmd.extend([
        "--days",
        str(req.days),
        "--limit",
        str(req.limit),
        "--sleep",
        str(req.sleep_s),
        "--out-csv",
        job.out_csv,
        "--out-json",
        job.out_json,
    ])

    if (req.pdf_mode or "").strip():
        cmd.extend(["--pdf-mode", str(req.pdf_mode).strip()])

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        cmd.extend(["--db-url", db_url])
    else:
        cmd.append("--no-db")

    if req.recording_numbers:
        # Bypass Playwright search by explicitly supplying recording numbers.
        for rn in req.recording_numbers:
            rn = (rn or "").strip()
            if rn:
                cmd.extend(["--recording-number", rn])
    elif not req.use_search:
        raise RuntimeError("use_search=false requires recording_numbers")

    if req.only_new:
        cmd.append("--only-new")
    if req.metadata_only:
        cmd.append("--metadata-only")
    if req.use_proxy:
        cmd.append("--use-proxy")

    try:
        log_path = Path(job.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(" ".join(cmd) + "\n")
            logf.flush()

            # Run in repo root (where storage_state.json lives)
            proc = subprocess.run(
                cmd,
                cwd=str(Path.cwd()),
                stdout=logf,
                stderr=logf,
                text=True,
                timeout=60 * 60,
            )
        if proc.returncode != 0:
            raise RuntimeError(f"scraper exit {proc.returncode} (see job log)")

        # Promote this job's outputs to be the new "latest".
        try:
            latest_csv = _default_csv_path()
            latest_csv.parent.mkdir(parents=True, exist_ok=True)

            src_csv = Path(job.out_csv)
            src_json = Path(job.out_json)
            if src_csv.exists():
                tmp_csv = latest_csv.with_suffix(latest_csv.suffix + ".tmp")
                shutil.copyfile(src_csv, tmp_csv)
                tmp_csv.replace(latest_csv)
            if src_json.exists():
                latest_json = latest_csv.with_suffix(".json")
                tmp_json = latest_json.with_suffix(latest_json.suffix + ".tmp")
                shutil.copyfile(src_json, tmp_json)
                tmp_json.replace(latest_json)
        except Exception:
            # Non-fatal: job artifacts still exist under output/jobs.
            pass

        with _jobs_lock:
            _jobs[job.id].status = "done"
    except Exception as e:
        with _jobs_lock:
            _jobs[job.id].status = "error"
            _jobs[job.id].error = str(e)


def _run_arizona_scraper_subprocess(job: ArizonaJob, req: ArizonaApacheRunRequest) -> None:
    with _arizona_jobs_lock:
        _arizona_jobs[job.id].status = "running"

    cmd = [
        _python_bin(),
        "-m",
        "automation.arizona.scraper",
        "--page",
        str(req.page),
        "--page-size",
        str(req.page_size),
        "--sort",
        str(req.sort),
        "--dir",
        str(req.dir),
        "--max-pages",
        str(req.max_pages),
        "--max-records",
        str(req.max_records),
        "--timeout-s",
        str(req.timeout_s),
        "--out-csv",
        job.out_csv,
        "--out-json",
        job.out_json,
    ]

    if (req.results_url or "").strip():
        cmd.extend(["--results-url", str(req.results_url).strip()])
    if (req.search_id or "").strip():
        cmd.extend(["--search-id", str(req.search_id).strip()])
    if req.search_form:
        cmd.extend(["--search-form-json", json.dumps(req.search_form)])
    if (req.begin_date or "").strip():
        cmd.extend(["--begin-date", str(req.begin_date).strip()])
    if (req.end_date or "").strip():
        cmd.extend(["--end-date", str(req.end_date).strip()])
    if int(req.chunk_months or 1) > 0:
        cmd.extend(["--chunk-months", str(req.chunk_months)])
    if (req.begin_date_field or "").strip():
        cmd.extend(["--begin-date-field", str(req.begin_date_field).strip()])
    if (req.end_date_field or "").strip():
        cmd.extend(["--end-date-field", str(req.end_date_field).strip()])
    if not req.include_details:
        cmd.append("--no-details")
    if req.use_proxy:
        cmd.append("--use-proxy")

    try:
        log_path = Path(job.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(" ".join(cmd) + "\n")
            logf.flush()
            proc = subprocess.run(
                cmd,
                cwd=str(Path.cwd()),
                stdout=logf,
                stderr=logf,
                text=True,
                timeout=60 * 60,
            )
        if proc.returncode != 0:
            raise RuntimeError(f"Arizona scraper exit {proc.returncode} (see job log)")

        with _arizona_jobs_lock:
            _arizona_jobs[job.id].status = "done"
    except Exception as e:
        with _arizona_jobs_lock:
            _arizona_jobs[job.id].status = "error"
            _arizona_jobs[job.id].error = str(e)



@app.get("/health")
def health() -> dict:
    p = _default_csv_path()
    return {"ok": True, "csvPath": str(p), "csvExists": p.exists()}


@app.get("/cities")
def cities() -> dict:
    return {"cities": ARIZONA_CITIES}


@app.get("/csv/latest")
def csv_latest(city: Optional[str] = None, cities: Optional[str] = None) -> Response:
    p = _default_csv_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail="CSV not found")

    # Store JSON rows alongside the CSV so we can filter without re-parsing CSV.
    # If it's missing, we fall back to returning the CSV as-is.
    json_path = p.with_suffix(".json")
    city_list: list[str] = []
    if city:
        city_list = [city]
    elif cities:
        city_list = [c.strip() for c in cities.split(",") if c.strip()]

    if city_list and json_path.exists():
        rows = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(rows, list):
            raise HTTPException(status_code=500, detail="Bad rows JSON")
        filtered = filter_by_cities(rows, city_list)
        csv_text = render_csv_string(filtered)
        return Response(content=csv_text, media_type="text/csv")

    return Response(content=p.read_bytes(), media_type="text/csv")


@app.post("/run")
def run_scrape(
    req: RunRequest,
    wait: bool = False,
    timeout_s: int = 600,
    x_api_token: Optional[str] = Header(default=None),
) -> Response:
    """Trigger an on-demand scrape.

    - If `wait=true`, blocks until completion (or timeout) and returns CSV.
    - Otherwise returns a job id; fetch CSV via `/jobs/{id}/csv`.
    """

    _require_token(x_api_token)

    job_id = uuid4().hex
    out_dir = Path("output/jobs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = str(out_dir / f"{job_id}.csv")
    out_json = str(out_dir / f"{job_id}.json")
    log_path = str(out_dir / f"{job_id}.log")

    job = Job(
        id=job_id,
        status="queued",
        created_at=time.time(),
        out_csv=out_csv,
        out_json=out_json,
        log_path=log_path,
        cities=req.cities,
    )
    with _jobs_lock:
        _jobs[job_id] = job

    t = threading.Thread(target=_run_scraper_subprocess, args=(job, req), daemon=True)
    t.start()

    if not wait:
        return Response(
            content=json.dumps({"jobId": job_id, "status": "queued"}),
            media_type="application/json",
        )

    deadline = time.time() + max(1, int(timeout_s))
    while time.time() < deadline:
        with _jobs_lock:
            st = _jobs[job_id].status
            err = _jobs[job_id].error
        if st == "done":
            return jobs_csv(job_id, cities=",".join(req.cities) if req.cities else None)
        if st == "error":
            raise HTTPException(status_code=500, detail=err or "job failed")
        time.sleep(0.5)

    return Response(
        content=json.dumps({"jobId": job_id, "status": "running"}),
        media_type="application/json",
        status_code=202,
    )


@app.get("/jobs/{job_id}")
def jobs_status(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "jobId": job.id,
        "status": job.status,
        "error": job.error,
        "outCsv": job.out_csv,
        "outJson": job.out_json,
        "logPath": job.log_path,
    }


@app.get("/jobs/{job_id}/log")
def jobs_log(job_id: str, max_bytes: int = 200_000) -> Response:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    p = Path(job.log_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="job log not found")

    data = p.read_bytes()
    if len(data) > max(1, int(max_bytes)):
        data = data[-int(max_bytes) :]
    return Response(content=data, media_type="text/plain")


@app.get("/jobs/{job_id}/csv")
def jobs_csv(job_id: str, cities: Optional[str] = None) -> Response:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"job not done (status={job.status})")

    csv_path = Path(job.out_csv)
    json_path = Path(job.out_json)
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="job CSV not found")

    city_list = [c.strip() for c in (cities or "").split(",") if c.strip()]
    if city_list and json_path.exists():
        rows = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(rows, list):
            filtered = filter_by_cities(rows, city_list)
            return Response(content=render_csv_string(filtered), media_type="text/csv")

    return Response(content=csv_path.read_bytes(), media_type="text/csv")


# ===========================================================================
# API v1 — Production endpoints
# ===========================================================================


def _make_job(req: RunRequest) -> Job:
    """Create a Job record and start the scraper subprocess in a background thread."""
    job_id = uuid4().hex
    out_dir = Path("output/jobs")
    out_dir.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=job_id,
        status="queued",
        created_at=time.time(),
        out_csv=str(out_dir / f"{job_id}.csv"),
        out_json=str(out_dir / f"{job_id}.json"),
        log_path=str(out_dir / f"{job_id}.log"),
        cities=req.cities,
    )
    with _jobs_lock:
        _jobs[job_id] = job
    threading.Thread(target=_run_scraper_subprocess, args=(job, req), daemon=True).start()
    return job


def _make_arizona_job(req: ArizonaApacheRunRequest) -> ArizonaJob:
    job_id = uuid4().hex
    out_dir = Path("output/arizona_jobs")
    out_dir.mkdir(parents=True, exist_ok=True)
    job = ArizonaJob(
        id=job_id,
        status="queued",
        created_at=time.time(),
        out_csv=str(out_dir / f"{job_id}.csv"),
        out_json=str(out_dir / f"{job_id}.json"),
        log_path=str(out_dir / f"{job_id}.log"),
    )
    with _arizona_jobs_lock:
        _arizona_jobs[job_id] = job
    threading.Thread(target=_run_arizona_scraper_subprocess, args=(job, req), daemon=True).start()
    return job


@app.post(
    "/api/v1/scrape",
    summary="Start a scrape job",
    tags=["v1"],
    status_code=202,
)
def api_v1_scrape(
    req: RunRequest,
    x_api_token: Optional[str] = Header(default=None),
) -> dict:
    """
    Discover and process all Maricopa County Recorder documents for a given date range.

    **What it does (in order):**
    1. Calls `GET https://publicapi.recorder.maricopa.gov/documents/search` with
       pagination to discover every recording number in the date range.
    2. For each recording number, calls the metadata endpoint
       `GET https://publicapi.recorder.maricopa.gov/documents/{recordingNumber}`.
    3. Skips any record that has no document type (broken/restricted).
    4. Downloads each PDF in-memory and runs Tesseract OCR — no files saved to disk.
    5. Stores metadata + full OCR text in Supabase (`documents` table).
    6. Extracts structured fields (trustor names, address, sale date, principal balance)
       and stores them in the `properties` table.

    **Date range options (choose one):**
    - `begin_date` + `end_date` — explicit range, e.g. `2026-03-05` → `2026-03-05`
    - `end_date` + `days` — N days ending on `end_date`
    - `days` only — N days ending today

    **Set `limit: 0` to process every document found (no cap).**
    **Set `document_code: "ALL"` for all document types.**

    Returns a `jobId` immediately. The job runs in the background.
    Poll `GET /api/v1/jobs/{jobId}` to track progress.
    """
    _require_token(x_api_token)
    job = _make_job(req)
    base = f"/api/v1/jobs/{job.id}"
    return {
        "jobId": job.id,
        "status": "queued",
        "message": (
            f"Scrape job queued — "
            f"{req.begin_date or 'today'} → {req.end_date or 'today'} — "
            f"doc_code={req.document_code} limit={req.limit or 'none'}"
        ),
        "statusUrl": base,
        "logUrl": f"{base}/log",
        "resultsUrl": f"{base}/results",
        "supabaseTable": "documents",
    }


@app.post(
    "/api/v1/arizona/apache/scrape",
    summary="Start an Apache County Assessor scrape job",
    tags=["v1", "arizona"],
    status_code=202,
)
def api_v1_arizona_apache_scrape(
    req: ArizonaApacheRunRequest,
    x_api_token: Optional[str] = Header(default=None),
) -> dict:
    """Start a real-time Apache County Assessor scrape job.

    Requirements:
    - Set `APACHE_ASSESSOR_USERNAME` and `APACHE_ASSESSOR_PASSWORD` in the environment.
    - Pass either `results_url` or `search_id`.
    - Use `results_url` when you already have a logged-in search results page.
    """
    _require_token(x_api_token)

    if not (req.results_url or "").strip() and not (req.search_id or "").strip() and not req.search_form:
        if not ((req.begin_date or "").strip() and (req.end_date or "").strip() and req.search_form):
            raise HTTPException(status_code=422, detail="Pass results_url, search_id, search_form, or timeline begin_date/end_date with search_form")
    if not os.environ.get("APACHE_ASSESSOR_USERNAME", "").strip() or not os.environ.get("APACHE_ASSESSOR_PASSWORD", "").strip():
        raise HTTPException(status_code=500, detail="Missing APACHE_ASSESSOR_USERNAME / APACHE_ASSESSOR_PASSWORD")

    job = _make_arizona_job(req)
    base = f"/api/v1/arizona/jobs/{job.id}"
    return {
        "jobId": job.id,
        "status": "queued",
        "message": "Apache County Assessor scrape job queued",
        "statusUrl": base,
        "logUrl": f"{base}/log",
        "resultsUrl": f"{base}/results",
        "csvUrl": f"{base}/csv",
        "constraints": {
            "authRequired": True,
            "requiresResultsUrlOrSearchId": False,
            "supportsSearchPost": True,
            "supportsProxy": True,
        },
    }


@app.post(
    "/api/v1/arizona/apache/properties/scrape",
    summary="Start an Apache County property timeline scrape job",
    tags=["v1", "arizona", "properties"],
    status_code=202,
)
def api_v1_arizona_apache_properties_scrape(
    req: ArizonaApacheRunRequest,
    x_api_token: Optional[str] = Header(default=None),
) -> dict:
    """Start a property-focused Apache County scrape across a timeline.

    Use this endpoint for structured property leads such as owner name,
    grantee name, sale price, area, sale date, parcel number, and address.
    """
    if not (req.begin_date or "").strip() or not (req.end_date or "").strip():
        raise HTTPException(status_code=422, detail="begin_date and end_date are required for property timeline scraping")
    if not req.search_form:
        raise HTTPException(status_code=422, detail="search_form is required for property timeline scraping")

    response = api_v1_arizona_apache_scrape(req, x_api_token=x_api_token)
    response["message"] = "Apache County property timeline scrape job queued"
    response["timeline"] = {
        "beginDate": req.begin_date,
        "endDate": req.end_date,
        "chunkMonths": req.chunk_months,
    }
    response["propertiesUrl"] = response["resultsUrl"]
    return response


@app.get(
    "/api/v1/arizona/jobs/{job_id}",
    summary="Arizona job status",
    tags=["v1", "arizona"],
)
def api_v1_arizona_job_status(job_id: str) -> dict:
    with _arizona_jobs_lock:
        job = _arizona_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    base = f"/api/v1/arizona/jobs/{job_id}"
    return {
        "jobId": job.id,
        "status": job.status,
        "error": job.error,
        "createdAt": job.created_at,
        "statusUrl": base,
        "logUrl": f"{base}/log",
        "resultsUrl": f"{base}/results",
        "csvUrl": f"{base}/csv",
    }


@app.get(
    "/api/v1/arizona/jobs/{job_id}/log",
    summary="Arizona job log",
    tags=["v1", "arizona"],
)
def api_v1_arizona_job_log(job_id: str, max_bytes: int = 200_000) -> Response:
    with _arizona_jobs_lock:
        job = _arizona_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    p = Path(job.log_path)
    if not p.exists():
        return Response(content="(log not available yet)", media_type="text/plain")
    data = p.read_bytes()
    if len(data) > max(1, int(max_bytes)):
        data = data[-int(max_bytes):]
    return Response(content=data, media_type="text/plain")


@app.get(
    "/api/v1/arizona/jobs/{job_id}/results",
    summary="Arizona job JSON results",
    tags=["v1", "arizona"],
)
def api_v1_arizona_job_results(job_id: str) -> Response:
    with _arizona_jobs_lock:
        job = _arizona_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is not finished yet (status={job.status})")
    json_path = Path(job.out_json)
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Results JSON not found")
    return Response(content=json_path.read_bytes(), media_type="application/json")


@app.get(
    "/api/v1/arizona/jobs/{job_id}/csv",
    summary="Arizona job CSV results",
    tags=["v1", "arizona"],
)
def api_v1_arizona_job_csv(job_id: str) -> Response:
    with _arizona_jobs_lock:
        job = _arizona_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is not finished yet (status={job.status})")
    csv_path = Path(job.out_csv)
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Results CSV not found")
    return Response(content=csv_path.read_bytes(), media_type="text/csv")


@app.get(
    "/api/v1/jobs/{job_id}",
    summary="Job status",
    tags=["v1"],
)
def api_v1_job_status(job_id: str) -> dict:
    """Check the status of a running or completed scrape job.

    `status` values:
    - `queued`  — job is waiting to start
    - `running` — scraper subprocess is active
    - `done`    — completed successfully; data is in Supabase
    - `error`   — subprocess exited non-zero; see `error` field
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    base = f"/api/v1/jobs/{job_id}"
    return {
        "jobId": job.id,
        "status": job.status,
        "error": job.error,
        "createdAt": job.created_at,
        "statusUrl": base,
        "logUrl": f"{base}/log",
        "resultsUrl": f"{base}/results",
    }


@app.get(
    "/api/v1/jobs/{job_id}/log",
    summary="Live job log",
    tags=["v1"],
)
def api_v1_job_log(job_id: str, max_bytes: int = 200_000) -> Response:
    """Stream the live log output of a running or completed scrape job.

    Returns plain text. Call repeatedly while `status == "running"` to tail the log.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    p = Path(job.log_path)
    if not p.exists():
        return Response(content="(log not available yet)", media_type="text/plain")
    data = p.read_bytes()
    if len(data) > max(1, int(max_bytes)):
        data = data[-int(max_bytes):]
    return Response(content=data, media_type="text/plain")


@app.get(
    "/api/v1/jobs/{job_id}/results",
    summary="Download results CSV",
    tags=["v1"],
)
def api_v1_job_results(job_id: str, cities: Optional[str] = None) -> Response:
    """Download the results of a **completed** scrape as a CSV file.

    Optionally filter rows by city with `?cities=Scottsdale,Phoenix`.

    Note: all data is also stored in Supabase in real-time during the job,
    so this CSV is provided as a convenience export only.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not finished yet (status={job.status}). Poll GET /api/v1/jobs/{job_id} and retry when done.",
        )
    csv_path = Path(job.out_csv)
    json_path = Path(job.out_json)
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="Results CSV not found (job may have produced 0 records)")
    city_list = [c.strip() for c in (cities or "").split(",") if c.strip()]
    if city_list and json_path.exists():
        rows = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(rows, list):
            filtered = filter_by_cities(rows, city_list)
            return Response(content=render_csv_string(filtered), media_type="text/csv")
    return Response(content=csv_path.read_bytes(), media_type="text/csv")


@app.get(
    "/api/v1/health",
    summary="Health + DB connectivity check",
    tags=["v1"],
)
def api_v1_health() -> dict:
    """Returns server health and Supabase DB connectivity status."""
    import psycopg  # local import to avoid startup cost

    db_ok = False
    db_error: Optional[str] = None
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        try:
            conn = psycopg.connect(db_url, connect_timeout=5)
            conn.close()
            db_ok = True
        except Exception as exc:
            db_error = str(exc)

    with _jobs_lock:
        active = sum(1 for j in _jobs.values() if j.status in ("queued", "running"))
        total = len(_jobs)

    return {
        "ok": True,
        "version": "1.0.0",
        "dbConfigured": bool(db_url),
        "dbConnected": db_ok,
        "dbError": db_error,
        "activeJobs": active,
        "totalJobsSinceStart": total,
    }


@app.post(
    "/api/v1/llm/extract",
    summary="Extract structured property fields from OCR text",
    tags=["v1", "llm"],
)
def api_v1_llm_extract(
    req: LlmExtractRequest,
    x_api_token: Optional[str] = Header(default=None),
) -> dict:
    """Hosted Groq extraction endpoint for reuse by county pipelines.

    Clients can call this endpoint instead of calling Groq directly.
    """
    _require_token(x_api_token)

    fields = extract_fields_llm_direct(
        req.ocr_text,
        fallback_to_rule_based=req.fallback_to_rule_based,
    )
    return {
        "ok": True,
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "fields": asdict(fields),
    }


@app.post(
    "/api/v1/llm/extract-document",
    summary="Extract structured fields from a PDF document",
    tags=["v1", "llm"],
)
def api_v1_llm_extract_document(
    req: LlmExtractDocumentRequest,
    x_api_token: Optional[str] = Header(default=None),
) -> dict:
    """Hosted PDF -> OCR -> Groq extraction endpoint.

    This endpoint allows workers to avoid local OCR and send the document directly.
    """
    _require_token(x_api_token)

    try:
        pdf_bytes = base64.b64decode(req.pdf_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid pdf_base64 payload: {exc}")

    if not pdf_bytes:
        raise HTTPException(status_code=422, detail="Empty PDF payload")

    try:
        ocr_text = ocr_pdf_bytes_to_text(pdf_bytes)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}")

    fields = extract_fields_llm_direct(
        ocr_text,
        fallback_to_rule_based=req.fallback_to_rule_based,
    )
    return {
        "ok": True,
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "recording_number": req.recording_number,
        "ocr_chars": len(ocr_text),
        "ocr_text": ocr_text,
        "fields": asdict(fields),
    }
