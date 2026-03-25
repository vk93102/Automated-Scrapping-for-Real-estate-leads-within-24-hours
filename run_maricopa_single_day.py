#!/usr/bin/env python3
"""
Production-level Maricopa County single-day pipeline runner.
Executes a single day scrape with proper error handling and output generation.
"""

import sys
import os
import subprocess
from pathlib import Path
from datetime import date, timedelta
import json

# Ensure we're in the project root
PROJECT_ROOT = Path(__file__).parent.absolute()
os.chdir(PROJECT_ROOT)

# Add project to path
sys.path.insert(0, str(PROJECT_ROOT))

def run_maricopa_pipeline():
    """Run the Maricopa County pipeline for the last day."""
    
    # Calculate dates: yesterday (last 1 day)
    end_date = date.today()
    begin_date = end_date - timedelta(days=1)
    
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Command line arguments for the pipeline
    cmd = [
        sys.executable,
        "-m",
        "maricopa.scraper",
        "--days", "1",  # Last 1 day only
        "--document-code", "ALL",  # Get all document types
        "--limit", "0",  # No limit
        "--sleep", "0.5",  # 500ms between requests
        "--workers", "4",  # 4 worker threads for OCR/LLM
        "--out-json", str(output_dir / "output.json"),
        "--out-csv", str(output_dir / "new_records_latest.csv"),
        "--csv-include-meta",  # Include metadata in CSV
        "--out-csv-dated",  # Also create dated CSV
        "--only-new",  # Skip already-seen recordings
        "--pdf-mode", "memory",  # Don't save PDFs to disk
        "--log-level", "INFO",
    ]
    
    # Add DB flag if DATABASE_URL is set
    if not os.environ.get("DATABASE_URL", "").strip():
        cmd.append("--no-db")
    
    print(f"Starting Maricopa County pipeline for {begin_date} to {end_date}")
    print(f"Date range: {begin_date.isoformat()} to {end_date.isoformat()}")
    print(f"Output directory: {output_dir}")
    print(f"Command: {' '.join(cmd)}")
    print("-" * 80)
    
    try:
        result = subprocess.run(cmd, check=False, cwd=PROJECT_ROOT)
        
        if result.returncode != 0:
            print("-" * 80)
            print(f"Pipeline exited with status code: {result.returncode}")
            return False
        
        print("-" * 80)
        print("Pipeline completed successfully.")
        
        # Verify output files
        print("\nVerifying output files...")
        json_path = output_dir / "output.json"
        csv_path = output_dir / "new_records_latest.csv"
        
        if json_path.exists():
            with open(json_path, "r") as f:
                data = json.load(f)
            print(f"✓ JSON output: {json_path} ({len(data)} records)")
        else:
            print(f"✗ JSON output not found: {json_path}")
        
        if csv_path.exists():
            with open(csv_path, "r") as f:
                lines = f.readlines()
            print(f"✓ CSV output: {csv_path} ({len(lines) - 1} records + header)")
        else:
            print(f"✗ CSV output not found: {csv_path}")
        
        return True
        
    except Exception as e:
        print(f"Error running pipeline: {e}")
        return False

if __name__ == "__main__":
    success = run_maricopa_pipeline()
    sys.exit(0 if success else 1)
