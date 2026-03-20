import sys
with open("navajo/run_navajo_interval.py", "r") as f: content = f.read()
content = content.replace("def _run_once(interval_doc_types: list[str], workers: int, lookback_days: int) -> tuple[int, int, int]:", "def _run_once(interval_doc_types: list[str], workers: int, lookback_days: int, ocr_limit: int=0) -> tuple[int, int, int]:")
content = content.replace("total, inserted, updated = _run_once(args.doc_types, args.workers, args.lookback_days)", "total, inserted, updated = _run_once(args.doc_types, args.workers, args.lookback_days, args.ocr_limit)")
content = content.replace("ocr_limit=args.ocr_limit,", "ocr_limit=ocr_limit,")
with open("navajo/run_navajo_interval.py", "w") as f: f.write(content)
