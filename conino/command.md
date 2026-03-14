# ── FULL PIPELINE (use this) ─────────────────────────────────────────────────
# live_pipeline.py:
#   Stage 1  Playwright → fresh session cookies
#   Stage 2  POST /searchPost + paginate all pages (requests, not Playwright)
#   Stage 3  Client-side filter for target doc types
#   Stage 4  Real-time table: fee#, date, doc ID, type, grantor → grantee
#   Stage 5  Detail page → propertyAddress + principalAmount
#   Stage 6  PDF download via GUID URL (fixed — no more DEGRADED-* broken URLs)
#   Stage 7  OCR (pdftotext / tesseract) + Groq Llama-3 extraction
#   Stage 8  Save enriched CSV

cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino && \
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py \
  --start-date "2/13/2026" \
  --end-date "3/13/2026" \
  --ocr-limit 20 2>&1

# ── OPTIONS ───────────────────────────────────────────────────────────────────
# --pages 3          fetch only 3 pages (for a quick test)
# --ocr-limit 0      skip OCR entirely (fast metadata-only run)
# --no-groq          use regex extraction instead of Groq Llama-3
# --headful          show Playwright browser window
# --csv-name foo.csv custom output name

# ── QUICK TEST (page 1 only, no OCR) ─────────────────────────────────────────
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino && \
/Users/vishaljha/.pyenv/versions/3.10.13/bin/python live_pipeline.py \
  --start-date "2/13/2026" \
  --end-date "3/13/2026" \
  --pages 1 \
  --ocr-limit 0 2>&1




vishaljha@vishals-MacBook-Air conino %  /Users/vishaljha/.pyenv/versions/3.10.13/bin/python /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours/conino/live_pipeline.py --start-date "2/13/2026" --end-date "3/13/2026" --pages 1 --ocr-limit 3 --no-groq 2>&1