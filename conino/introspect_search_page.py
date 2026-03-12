from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

BASE_URL = "https://eagleassessor.coconino.az.gov:8444"
SEARCH_URL = f"{BASE_URL}/web/search/DOCSEARCH1213S1"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    cookie = os.environ.get("COCONINO_COOKIE", "").strip()
    if not cookie:
        raise RuntimeError("Set COCONINO_COOKIE before running this script")

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": SEARCH_URL,
        "Cookie": cookie,
        "Connection": "keep-alive",
    }
    request = Request(SEARCH_URL, headers=headers, method="GET")
    with urlopen(request, timeout=60) as response:
        html = response.read().decode("utf-8", errors="ignore")

    html_path = OUTPUT_DIR / "search_page_live.html"
    html_path.write_text(html, encoding="utf-8")

    forms = []
    for form_match in re.finditer(r"<form\b([\s\S]*?)</form>", html, flags=re.IGNORECASE):
        form_html = form_match.group(0)
        open_tag = re.search(r"<form\b([^>]*)>", form_html, flags=re.IGNORECASE)
        attrs = open_tag.group(1) if open_tag else ""
        action = re.search(r'action="([^"]*)"', attrs, flags=re.IGNORECASE)
        method = re.search(r'method="([^"]*)"', attrs, flags=re.IGNORECASE)
        form_id = re.search(r'id="([^"]*)"', attrs, flags=re.IGNORECASE)
        names = []
        for input_match in re.finditer(r"<(input|select|textarea)\b([^>]*)>", form_html, flags=re.IGNORECASE):
            tag = input_match.group(1).lower()
            tag_attrs = input_match.group(2)
            name_match = re.search(r'name="([^"]*)"', tag_attrs, flags=re.IGNORECASE)
            value_match = re.search(r'value="([^"]*)"', tag_attrs, flags=re.IGNORECASE)
            type_match = re.search(r'type="([^"]*)"', tag_attrs, flags=re.IGNORECASE)
            if name_match:
                names.append(
                    {
                        "tag": tag,
                        "name": name_match.group(1),
                        "type": type_match.group(1) if type_match else "",
                        "value": value_match.group(1) if value_match else "",
                    }
                )
        forms.append(
            {
                "id": form_id.group(1) if form_id else "",
                "action": action.group(1) if action else "",
                "method": method.group(1) if method else "",
                "fields": names,
            }
        )

    payload = {
        "htmlPath": str(html_path),
        "title": re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL).group(1).strip() if re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL) else "",
        "forms": forms,
        "hasSearchPost": "/web/searchPost/DOCSEARCH1213S1" in html,
        "hasResultsRoute": "/web/searchResults/DOCSEARCH1213S1" in html,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
