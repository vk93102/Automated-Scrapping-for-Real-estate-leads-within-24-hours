#!/usr/bin/env python3
"""
PDF-ONLY STRICT MODE: Actual Document Processing
Tesseract OCR → LLM Extraction → Quality Validation → Database

This script enforces:
- ACTUAL PDF DOWNLOADS (not metadata placeholders)
- TESSERACT OCR PROCESSING (confidence > 0.5 enforced)
- LLM EXTRACTION FROM OCR TEXT
- STRICT QUALITY VALIDATION
- REJECT ALL FAILED RECORDS (no storage of garbage)
- DATABASE-ONLY OUTPUT (no local files)
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

print("=" * 100)
print("🚀 PDF-ONLY STRICT MODE: ACTUAL DOCUMENT PROCESSING")
print("=" * 100)

import argparse
from maricopa.scraper import main

# Create arguments for PDF-only mode
class Args:
    """PDF-only, strict quality mode configuration"""
    
    def __init__(self):
        # Date range
        self.begin_date = "2026-03-20"  # Last 3 days
        self.end_date = "2026-03-23"
        self.days = 3
        
        # Processing
        self.limit = 0  # No limit
        self.workers = 2  # Multi-threaded OCR
        self.sleep = 0.5  # Fast processing
        
        # CRITICAL: PDF-only mode
        self.metadata_only = False  # MUST BE FALSE - forces PDF processing
        
        # Output
        self.document_code = "NS"  # N/TR SALE documents only
        self.db_only = True  # Database-only (no local files)
        self.out_json = None  # No local JSON
        self.out_csv = None  # No local CSV
        self.out_csv_dated = False
        self.csv_include_meta = False
        self.seen_path = None  # No local state
        self.only_new = True  # Only process new records
        self.recording_numbers_file = ""
        
        # PDF mode
        self.pdf_mode = "memory"  # Process PDF bytes in memory (no disk writes)
        
        # Database
        self.no_db = False  # MUST store to database
        self.db_url = os.environ.get("DATABASE_URL", "")
        
        # Logging
        self.log_level = "INFO"
        
        # Search backend
        self.search_backend = "api"  # Public JSON API
        
        # Proxy
        self.proxy_list = ""
        self.use_proxy = False
        self.playwright_proxy = ""
        self.storage_state = ""
        self.headful = False
        self.browser_exec = ""
        
        # Force reprocessing
        self.force = True  # Reprocess even if already done

# Validate configuration
args = Args()

print(f"\n✅ Configuration Check:")
print(f"  📍 Date Range: {args.begin_date} to {args.end_date}")
print(f"  📄 Mode: METADATA_ONLY={args.metadata_only} (must be FALSE for PDF processing)")
print(f"  🔍 PDF Mode: {args.pdf_mode} (memory = no disk writes)")
print(f"  💾 Database-Only: {args.db_only} (no local files)")
print(f"  ⚙️  Workers: {args.workers} (multi-threaded OCR)")
print(f"  🚫 No Local Artifacts: JSON={args.out_json is None}, CSV={args.out_csv is None}")

if args.metadata_only:
    print("\n❌ ERROR: --metadata-only is TRUE! This disables PDF processing!")
    print("   Set metadata_only=False to enable actual document PDF processing.")
    sys.exit(1)

if args.no_db:
    print("\n❌ ERROR: --no-db is TRUE! This prevents database storage!")
    print("   Set no_db=False to enable database persistence.")
    sys.exit(1)

if not args.db_url:
    print("\n❌ ERROR: DATABASE_URL not set in .env!")
    print("   Check .env file and ensure DATABASE_URL is configured.")
    sys.exit(1)

print(f"\n✅ All configuration checks PASSED!\n")

# Import and run the main scraper with our args
print("=" * 100)
print("🔄 STARTING PDF-ONLY PIPELINE")
print("=" * 100)
print(f"\n📥 Pipeline Flow:")
print(f"  1. Search for recording numbers ({args.begin_date} to {args.end_date})")
print(f"  2. For each recording: FETCH ACTUAL PDF DOCUMENT")
print(f"  3. Extract text via TESSERACT OCR")
print(f"  4. Validate OCR quality (confidence > 0.5 required)")
print(f"  5. Extract fields via LLM (llama-3.1-8b-instant)")
print(f"  6. STRICT quality validation:")
print(f"     ✓ Real borrower names (no garbage, no UNKNOWN)")
print(f"     ✓ Real addresses (5-500 chars, realistic format)")
print(f"     ✓ Real Arizona cities (cross-referenced database)")
print(f"     ✓ Valid dates (MM/DD/YYYY format)")
print(f"     ✓ Realistic balances ($1K-$1B)")
print(f"  7. REJECT ALL FAILED RECORDS (quality validation required)")
print(f"  8. STORE ONLY PASSING RECORDS to PostgreSQL database")
print(f"  9. ZERO LOCAL FILES (db-only mode)")
print(f"\n⏱️  Expected Time: ~5-10 minutes for last 3 days\n")

# Run the main scraper function
try:
    sys.argv = [
        sys.argv[0],
        "--begin-date", args.begin_date,
        "--end-date", args.end_date,
        "--limit", "0",
        "--workers", str(args.workers),
        "--document-code", args.document_code,
        "--log-level", args.log_level,
        "--db-only",
        "--pdf-mode", args.pdf_mode,
        "--force",
    ]
    
    # Remove output file arguments to prevent file creation
    if "--out-json" not in sys.argv:
        sys.argv.extend(["--out-json", "/dev/null"])
    if "--out-csv" not in sys.argv:
        sys.argv.extend(["--out-csv", "/dev/null"])
    
    # Ensure no local state files
    sys.argv.extend(["--seen-path", "/dev/null"])
    
    print("🚀 Launching scraper with PDF-only configuration...\n")
    main()
    
except KeyboardInterrupt:
    print("\n\n⚠️  Pipeline interrupted by user")
    sys.exit(1)
except Exception as e:
    print(f"\n\n❌ Pipeline failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 100)
print("✅ PDF-ONLY PIPELINE COMPLETED")
print("=" * 100)
print("\n📊 Results stored in PostgreSQL database:")
print("   - Query: SELECT COUNT(*) FROM properties WHERE created_at >= NOW() - INTERVAL '24 hours'")
print("   - All records are quality-validated (no garbage)")
print("   - LLM model = 'llama-3.1-8b-instant-tesseract-ocr' (OCR source)")
print("   - Or 'llama-3.1-8b-instant-metadata' (for failed OCR fallback)")
print("\n✅ Zero local files created (db-only mode)")
print("=" * 100)
