from __future__ import annotations

import glob
import json
import re
from pathlib import Path


def main() -> int:
    paths = sorted(glob.glob("SANTA CRUZ/output/santacruz_leads_*.json"))
    print(f"json_files={len(paths)}")
    if not paths:
        return 0

    latest = Path(paths[-1])
    print(f"latest={latest}")

    data = json.loads(latest.read_text(encoding="utf-8"))
    records = data.get("records") or []
    print(f"records={len(records)}")

    doc_pop = sum(1 for r in records if str(r.get("documentUrls") or "").strip())
    links_pop = sum(1 for r in records if str(r.get("links") or "").strip())
    parcel_like = sum(
        1
        for r in records
        if re.match(r"^\s*parcel\s*id\b", str(r.get("propertyAddress") or ""), flags=re.I)
    )
    not_found = sum(1 for r in records if str(r.get("propertyAddress") or "").strip() == "NOT_FOUND")

    print(f"documentUrls_populated={doc_pop}")
    print(f"links_populated={links_pop}")
    print(f"parcel_like_addresses_remaining={parcel_like}")
    print(f"propertyAddress_NOT_FOUND={not_found}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
