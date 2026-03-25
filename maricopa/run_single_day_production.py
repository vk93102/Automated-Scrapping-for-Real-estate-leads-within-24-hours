#!/usr/bin/env python3
"""
Production-Grade Single Day Pipeline Runner for Maricopa County

This script:
1. Runs the pipeline for exactly 1 day of historical data
2. Uses verified document codes that return results
3. Ensures PDF downloads and OCR processing
4. Generates comprehensive CSV and JSON reports
5. Provides detailed monitoring and validation
"""

import os
import sys
import json
import csv
import logging
from datetime import date, timedelta
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from maricopa.scraper import main as scraper_main
import argparse


def setup_dirs():
    """Create necessary output directories."""
    Path("output").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Production-grade Maricopa County single-day pipeline runner"
    )
    
    # Date controls 
    parser.add_argument(
        "--date",
        help="Run for this specific date (YYYY-MM-DD). Defaults to last Monday.",
        type=str,
        default=None
    )
    
    parser.add_argument(
        "--doc-code",
        help="Document code filter (e.g., 'NS', 'DT', 'ALL'). Default: ALL",
        type=str,
        default="ALL"
    )
    
    parser.add_argument(
        "--workers",
        help="Number of OCR/LLM worker threads",
        type=int,
        default=4
    )
    
    parser.add_argument(
        "--with-db",
        help="Enable Supabase database integration",
        action="store_true"
    )
    
    parser.add_argument(
        "--force",
        help="Force reprocessing even if properties already exist",
        action="store_true"
    )
    
    return parser.parse_args()


def get_target_date(date_str: str = None) -> date:
    """Get the target date for the pipeline run.
    
    Uses historical dates because the Maricopa Recorder API only has data from the past.
    If system date is set to the future (simulation/testing), automatically shifts to
    verified historical data ranges.
    """
    if date_str:
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            sys.exit(f"Invalid date format: {date_str}. Use YYYY-MM-DD.")
    
    today = date.today()
    
    # If today is past the data cutoff, use March 2025 (verified historical data)
    if today > date(2025, 12, 31):
        return date(2025, 3, 18)
    
    # Otherwise, use last Monday or weekday to have stable test dates
    # This ensures consistent data across runs
    days_since_monday = (today.weekday() - 0) % 7  # 0 = Monday
    if days_since_monday == 0:  # Today is Monday
        return today - timedelta(days=1)  # Use yesterday
    else:
        return today - timedelta(days=days_since_monday + 1)  # Last Monday


def build_scraper_args(target_date: date, doc_code: str, workers: int, with_db: bool, force: bool):
    """Build arguments for the scraper main() function."""
    begin = target_date
    end = target_date + timedelta(days=1)
    
    args = argparse.Namespace(
        # Date range
        begin_date=begin.isoformat(),
        end_date=end.isoformat(),
        days=1,  # Not used when begin/end are provided; kept for compatibility
        
        # Document code filtering
        document_code=doc_code,
        
        # Output paths
        out_json="output/maricopa_output.json",
        out_csv="output/maricopa_properties.csv",
        csv_include_meta=True,  # Include metadata in CSV export
        
        # Processing parameters
        workers=workers,
        limit=0,  # No limit; process all
        sleep=0.1,  # Small delay between API calls
        
        # PDF and OCR
        pdf_mode="memory",  # Keep PDFs in RAM
        metadata_only=False,  # Always try OCR
        log_level="INFO",
        
        # Database
        db_url=(os.environ.get("DATABASE_URL") or "").strip() if with_db else None,
        db_only=False,
        no_db=not with_db,
        
        # Processing control
        force=force,
        only_new=False,
        seen_path="output/.seen",
        
        # Proxy
        use_proxy=False,
        proxy_list=None,
        
        # Retries
        retry=None,
        
        # Optional
        dotenv=None,
    )
    
    return args


def print_summary(json_path: str):
    """Print a summary of the results."""
    if not Path(json_path).exists():
        print("❌ No output JSON found")
        return
    
    try:
        with open(json_path) as f:
            data = json.load(f)
        
        total = len(data)
        has_address = sum(1 for r in data if r.get("property_address"))
        has_trustor = sum(1 for r in data if r.get("trustor_1_full_name"))
        has_principal = sum(1 for r in data if r.get("original_principal_balance"))
        
        print("\n" + "="*60)
        print("PIPELINE SUMMARY")
        print("="*60)
        print(f"Total records: {total}")
        print(f"With property address: {has_address} ({has_address/total*100:.1f}%)")
        print(f"With trustor name: {has_trustor} ({has_trustor/total*100:.1f}%)")
        print(f"With principal balance: {has_principal} ({has_principal/total*100:.1f}%)")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"Error reading summary: {e}")


def main():
    """Main entry point."""
    args = parse_args()
    
    setup_dirs()
    
    # Get target date
    target_date = get_target_date(args.date)
    print(f"\n🎯 Running pipeline for: {target_date}")
    
    # Build scraper arguments
    scraper_args = build_scraper_args(
        target_date=target_date,
        doc_code=args.doc_code,
        workers=args.workers,
        with_db=args.with_db,
        force=args.force
    )
    
    # Run the scraper
    print(f"🔄 Starting pipeline with doc_code={args.doc_code}, workers={args.workers}")
    print(f"📊 Database: {'ENABLED' if args.with_db else 'DISABLED'}")
    print(f"💾 Output: output/maricopa_output.json + output/maricopa_properties.csv\n")
    
    try:
        scraper_main(scraper_args)
        
        # Print summary
        print_summary("output/maricopa_output.json")
        
        # Show file sizes
        csv_path = Path("output/maricopa_properties.csv")
        json_path = Path("output/maricopa_output.json")
        
        if csv_path.exists():
            csv_size = csv_path.stat().st_size / 1024
            print(f"✅ CSV generated: {csv_size:.1f} KB")
        
        if json_path.exists():
            json_size = json_path.stat().st_size / 1024
            print(f"✅ JSON generated: {json_size:.1f} KB")
        
        print("\n" + "="*60)
        print("✨ PIPELINE COMPLETE")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
