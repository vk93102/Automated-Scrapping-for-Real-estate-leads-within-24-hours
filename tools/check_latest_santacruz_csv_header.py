from __future__ import annotations

import glob


def main() -> None:
    paths = sorted(glob.glob("SANTA CRUZ/output/santacruz_leads_*.csv"))
    if not paths:
        print("no_csv_files")
        return
    latest = paths[-1]
    print(f"latest={latest}")
    with open(latest, "r", encoding="utf-8") as f:
        header = (f.readline() or "").strip()
    print(f"has_documentUrls={'documentUrls' in header}")
    print(f"has_links={'links' in header}")
    print(f"header={header}")


if __name__ == "__main__":
    main()
