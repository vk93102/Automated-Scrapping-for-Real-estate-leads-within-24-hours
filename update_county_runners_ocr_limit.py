#!/usr/bin/env python3
"""
Add --ocr-limit parameter to all county interval runners.
This ensures consistent OCR extraction configuration across all counties.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent

# Counties to update
COUNTIES = {
    "cochise": "Cochise",
    "gila": "Gila",
    "navajo": "Navajo",
    "lapaz": "La Paz",
    "SANTA CRUZ": "Santa Cruz",
}

def add_ocr_limit_param(file_path: Path) -> bool:
    """Add --ocr-limit parameter to a runner file."""
    content = file_path.read_text()
    
    # Check if already has ocr-limit parameter
    if "--ocr-limit" in content:
        print(f"✓ {file_path.relative_to(ROOT)} already has --ocr-limit")
        return False
    
    # Find the _run_once function signature and update it
    pattern = r'def _run_once\(doc_types: list\[str\], workers: int, lookback_days: int, strict_llm: bool\) -> tuple\[int, int, int, int\]:'
    if pattern in content:
        new_sig = 'def _run_once(doc_types: list[str], workers: int, lookback_days: int, strict_llm: bool, ocr_limit: int) -> tuple[int, int, int, int]:'
        content = content.replace(pattern, new_sig)
    else:
        print(f"✗ Could not find _run_once signature in {file_path.relative_to(ROOT)}")
        return False
    
    # Update the pipeline call to use effective_ocr_limit
    # First add the logic to handle ocr_limit < 0
    run_once_body_pattern = r'(def _run_once.*?)\n(\s+)today = date\.today\(\)'
    
    # Find where to insert the ocr_limit check
    if "ocr_limit < 0" not in content:
        # Add the check after the schema setup
        insert_pattern = r'(\s+)with _connect_db\(db_url\) as conn:\n(\s+)_ensure_schema\(conn\)'
        insert_text = r'''\1with _connect_db(db_url) as conn:
\2_ensure_schema(conn)

\1# CRITICAL: ocr_limit controls extraction behavior:
\1#  -1 = skip OCR/LLM entirely (WRONG for data population - use for speed when data already exists)
\1#   0 = process ALL documents with OCR + Groq LLM (RECOMMENDED for backfill/new data)
\1#   N = process first N docs with OCR + Groq LLM (for testing)
\1# For proper data extraction, we MUST use ocr_limit=0
\1effective_ocr_limit = ocr_limit
\1if ocr_limit < 0:
\1    _log(f"warning: ocr_limit={ocr_limit} set to 0 for proper data extraction (trustor/trustee/address)")
\1    effective_ocr_limit = 0'''
        
        content = re.sub(insert_pattern, insert_text, content, count=1)
        
        # Replace ocr_limit=0 with ocr_limit=effective_ocr_limit in the pipeline call
        content = content.replace("ocr_limit=0,", "ocr_limit=effective_ocr_limit,")
    
    # Update main() to add the argument and pass it to _run_once
    if '--ocr-limit' not in content:
        # Add the argument parser line
        parser_pattern = r'(p\.add_argument\("--workers".*?\))\n(\s+)(p\.add_argument\("--once")'
        parser_insert = r'\1\n\2p.add_argument("--ocr-limit", type=int, default=0, help="0 means OCR+LLM for all records, -1 skip OCR")\n\2\3'
        content = re.sub(parser_pattern, parser_insert, content, count=1)
        
        # Update the _log call to include ocr_limit
        log_pattern = r'_log\(\s*f"starting \w+ interval runner interval_minutes=.*?workers=\{args\.workers\}"'
        if 'ocr_limit=' not in content[content.find('f"starting'):]:
            content = re.sub(
                r'(f"starting \w+ interval runner.*?workers=\{args\.workers\})"',
                r'\1 ocr_limit={args.ocr_limit}"',
                content,
                count=1
            )
        
        # Update the _run_once call to pass ocr_limit
        run_call_pattern = r'total, ins, upd, llm_used = _run_once\(args\.doc_types, args\.workers, args\.lookback_days, args\.strict_llm\)'
        new_run_call = r'total, ins, upd, llm_used = _run_once(args.doc_types, args.workers, args.lookback_days, args.strict_llm, args.ocr_limit)'
        content = content.replace(run_call_pattern, new_run_call)
    
    # Add extraction quality validation logs (similar to Graham)
    if "records_with_trustor" not in content:
        quality_check = '''    records = res.get("records", [])
    _log(f"processed {len(records)} documents; checking extraction quality...")
    
    # Validate data extraction
    records_with_trustor = len([r for r in records if (r.get("trustor") or "").strip()])
    records_with_groq = len([r for r in records if bool(r.get("usedGroq", False))])
    records_with_ocr = len([r for r in records if int(r.get("ocrChars", 0) or 0) > 0])
    
    _log(f"extraction quality: {records_with_ocr} with OCR text, {records_with_groq} used Groq LLM, {records_with_trustor} have trustor")
    
    if strict_llm:'''
        
        old_quality_check = '''    records = res.get("records", [])
    if strict_llm:'''
        
        content = content.replace(old_quality_check, quality_check)
    
    file_path.write_text(content)
    print(f"✓ Updated {file_path.relative_to(ROOT)}")
    return True


if __name__ == "__main__":
    print("Adding --ocr-limit parameter to county interval runners...\n")
    
    updated_count = 0
    for county_dir, county_name in COUNTIES.items():
        runner_file = ROOT / county_dir / f"run_{county_dir.lower().replace(' ', '')}_interval.py"
        
        # Handle special case for SANTA CRUZ
        if county_dir == "SANTA CRUZ":
            runner_file = ROOT / "SANTA CRUZ" / "run_santacruz_interval.py"
        
        if runner_file.exists():
            if add_ocr_limit_param(runner_file):
                updated_count += 1
        else:
            print(f"✗ File not found: {runner_file.relative_to(ROOT)}")
    
    print(f"\n✓ Updated {updated_count} county runners")
