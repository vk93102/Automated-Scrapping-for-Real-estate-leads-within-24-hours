#!/usr/bin/env python3
"""
PRODUCTION READY - PDF-ONLY END-TO-END PROCESSING
==================================================

All fixes applied:
✅ OCR BytesIO wrapped (PyPDF2 works)
✅ Lenient validation (filters only garbage)
✅ NoneType errors fixed
✅ pdf2image support (no ImageMagick required)

This will:
1. Fetch ACTUAL PDF documents (not metadata)
2. OCR with Tesseract 
3. Extract fields via LLM
4. Filter ONLY garbage records
5. Store to database only (no local files)

Borrower names, addresses, cities from REAL document content!
"""
import subprocess
import sys
import os
from datetime import datetime, timedelta

print("=" * 80)
print("🚀 PRODUCTION PDF-ONLY END-TO-END PIPELINE")
print("=" * 80)

# Configuration
os.environ['METADATA_ONLY'] = '0'        # Must be 0 for PDF processing
os.environ['PDF_MODE'] = 'memory'        # In-memory, no disk writes
os.environ['TESSERACT_OCR_ENABLED'] = '1'  # Force OCR
os.environ['STRICT_QUALITY_VALIDATION'] = '0'  # Use lenient validation

# Ask user for date range
print("\n📅 DATE RANGE OPTIONS:")
print("1. Last 3 days (quick test)")
print("2. Last 14 days")
print("3. Last 30 days (full production)") 
print("4. Custom range")

choice = input("\nSelect (1-4): ").strip()

if choice == '1':
    begin_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    print(f"✅ 3-day test: {begin_date} to {end_date}")
elif choice == '2':
    begin_date = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    print(f"✅ 14-day run: {begin_date} to {end_date}")
elif choice == '3':
    begin_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    print(f"✅ 30-day production: {begin_date} to {end_date}")
else:
    begin_date = input("Enter begin date (YYYY-MM-DD): ").strip()
    end_date = input("Enter end date (YYYY-MM-DD): ").strip()
    print(f"✅ Custom range: {begin_date} to {end_date}")

# Ask for workers
workers = input("\nNumber of workers (default 4): ").strip() or "4"

print("\n" + "=" * 80)
print("📊 PIPELINE CONFIGURATION:")
print(f"  Date Range:  {begin_date} → {end_date}")
print(f"  Workers:     {workers}")
print(f"  PDF Mode:    memory (no disk writes)")
print(f"  OCR:         Tesseract (actual document processing)")
print(f"  Validation:  Lenient (only garbage rejected)")
print(f"  Storage:     Database only")
print("=" * 80)

input("\nPress ENTER to START... ")

# Run pipeline
cmd = [
    sys.executable, '-m', 'maricopa.scraper',
    '--begin-date', begin_date,
    '--end-date', end_date,
    '--limit', '0',
    '--workers', workers,
    '--log-level', 'INFO',
    '--db-only',
    '--force',
    '--pdf-mode', 'memory'
]

print("\n🔄 STARTING PIPELINE...")
print(f"Command: {' '.join(cmd[3:])}\n")

try:
    result = subprocess.run(cmd, cwd='/Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours')
    sys.exit(result.returncode)
except KeyboardInterrupt:
    print("\n\n⚠️  Pipeline interrupted by user")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)
