# ── Stage 1: base ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# System deps: tesseract (OCR), poppler-utils (pdf2image), and curl (health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    curl \
    cron \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV TZ=America/Phoenix

# ── Stage 2: deps ──────────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 3: final ─────────────────────────────────────────────────────────
FROM deps AS final

WORKDIR /app

ENV TZ=America/Phoenix

# Copy project files
COPY . .

# Create runtime directories (logs, output, tmp)
RUN mkdir -p /app/logs /app/output /app/tmp

# ── Cron: run scraper every 20 minutes for NS documents in last 2 days ──────────────────────
# Uses Python directly — no bash file-I/O, no EDEADLK risk
RUN echo "*/20 * * * * root cd /app && DOC_CODE=NS DAYS_WINDOW=2 ./run_pipeline.sh >> /app/logs/cron_master.log 2>&1" \
    > /etc/cron.d/maricopa-scraper \
 && chmod 0644 /etc/cron.d/maricopa-scraper \
 && crontab /etc/cron.d/maricopa-scraper

# ── Expose API port ────────────────────────────────────────────────────────
EXPOSE 8080

# ── Entrypoint: start cron + API server together ──────────────────────────
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

CMD ["/docker-entrypoint.sh"]
