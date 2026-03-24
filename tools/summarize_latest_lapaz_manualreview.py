from __future__ import annotations

import glob
import json
from pathlib import Path


def main() -> int:
    paths = sorted(glob.glob("lapaz/output/lapaz_leads_*.json"))
    print(f"json_files={len(paths)}")
    if not paths:
        return 0

    chosen: Path | None = None
    chosen_records: list[dict] = []
    for p in reversed(paths):
        candidate = Path(p)
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        records = data.get("records") or []
        if isinstance(records, list) and records:
            chosen = candidate
            chosen_records = records
            break

    if not chosen:
        latest = Path(paths[-1])
        data = json.loads(latest.read_text(encoding="utf-8"))
        records = data.get("records") or []
        print(f"latest={latest}")
        print(f"records={len(records) if isinstance(records, list) else 0}")
        print("manualReview_true=0")
        return 0

    print(f"latest_non_empty={chosen}")
    records = chosen_records
    print(f"records={len(records)}")

    manual_true = [r for r in records if bool(r.get("manualReview"))]
    print(f"manualReview_true={len(manual_true)}")

    if manual_true:
        r = manual_true[0]
        print(f"example_documentId={r.get('documentId','')}")
        print(f"example_reasons={r.get('manualReviewReasons','')}")
        print(f"example_summary={str(r.get('manualReviewSummary',''))[:240]}")
        ctx = str(r.get("manualReviewContext") or "")
        print(f"example_context_len={len(ctx)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
