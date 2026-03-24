from __future__ import annotations

import glob
import json
import re
from pathlib import Path


def main() -> None:
    paths = sorted(glob.glob("SANTA CRUZ/output/santacruz_leads_*.json"))
    print(f"json_files={len(paths)}")
    if not paths:
        return

    latest = Path(paths[-1])
    print(f"latest={latest}")
    data = json.loads(latest.read_text(encoding="utf-8"))
    records = data.get("records") or []
    print(f"records={len(records)}")

    doc_pop = 0
    links_pop = 0
    parcel_not_found = 0
    parcel_like = 0
    sample_doc_urls: list[str] = []
    sample_links: list[str] = []

    for r in records:
        docu = str(r.get("documentUrls") or "").strip()
        if docu:
            doc_pop += 1
            if len(sample_doc_urls) < 3:
                sample_doc_urls.append(docu)

        links = str(r.get("links") or "").strip()
        if links:
            links_pop += 1
            if len(sample_links) < 2:
                sample_links.append(links)

        pa = str(r.get("propertyAddress") or "")
        if re.match(r"^\s*parcel\s*id\b", pa, flags=re.I):
            parcel_like += 1
        if pa.strip() == "NOT_FOUND":
            parcel_not_found += 1

    print(f"documentUrls_populated={doc_pop}")
    print(f"links_populated={links_pop}")
    print(f"parcel_like_addresses_remaining={parcel_like}")
    print(f"propertyAddress_NOT_FOUND={parcel_not_found}")

    for i, u in enumerate(sample_doc_urls, 1):
        print(f"sample_documentUrl_{i}={u}")

    for i, s in enumerate(sample_links, 1):
        # Don't print huge link blobs.
        print(f"sample_links_{i}={s[:240]}")


if __name__ == "__main__":
    main()
