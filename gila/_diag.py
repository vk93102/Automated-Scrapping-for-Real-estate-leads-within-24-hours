"""Diagnostic v2: inspect form HTML, find hidden tokens, probe POST variations."""
import re, requests, urllib.parse, time, sys

BASE      = "https://selfservice.gilacountyaz.gov"
SEARCH_ID = "DOCSEARCH2242S1"

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
})

# ── Step 1: GET form ──────────────────────────────────────────────────────────
r1 = s.get(f"{BASE}/web/search/{SEARCH_ID}", timeout=20)
s.cookies.set("disclaimerAccepted", "true", domain="selfservice.gilacountyaz.gov")
print("Step1 status    :", r1.status_code)
print("Cookies after GET:", dict(s.cookies))

# Inspect form: action, hidden fields, CSRF tokens
form_html = r1.text
form_action = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', form_html, re.IGNORECASE)
print("Form action     :", form_action.group(1) if form_action else "NOT FOUND")

hidden_inputs = re.findall(r'<input[^>]+type=["\']hidden["\'][^>]*>', form_html, re.IGNORECASE)
print(f"Hidden inputs ({len(hidden_inputs)}):")
for h in hidden_inputs[:20]:
    name  = re.search(r'name=["\']([^"\']+)["\']', h)
    value = re.search(r'value=["\']([^"\']*)["\']', h)
    print(f"  {name.group(1) if name else '?'} = {value.group(1) if value else '?'}")

# Look for any token / csrf in the whole page
csrf_m = re.search(r'(?:csrf|_token|viewstate|nonce)[^>]*value=["\']([^"\']+)["\']', form_html, re.IGNORECASE)
print("CSRF/token found:", csrf_m.group(1)[:40] if csrf_m else "none")
print()

# ── Step 2: POST ──────────────────────────────────────────────────────────────
doc_types = [
    "LIS PENDENS", "TRUSTEES DEED", "SHERIFFS DEED",
    "NOTICE OF TRUSTEES SALE", "TREASURERS DEED",
    "AMENDED STATE LIEN", "STATE LIEN", "STATE TAX LIEN", "RELEASE STATE TAX LIEN",
]

params = [
    ("field_BothNamesID-containsInput",                     "Contains Any"),
    ("field_BothNamesID",                                    ""),
    ("field_GrantorID-containsInput",                        "Contains Any"),
    ("field_GrantorID",                                      ""),
    ("field_GranteeID-containsInput",                        "Contains Any"),
    ("field_GranteeID",                                      ""),
    ("field_RecDateID_DOT_StartDate",                        "2/13/2026"),
    ("field_RecDateID_DOT_EndDate",                          "3/14/2026"),
    ("field_DocNumID",                                       ""),
    ("field_BookPageID_DOT_Book",                            ""),
    ("field_BookPageID_DOT_Page",                            ""),
    ("field_PlattedLegalID_DOT_Subdivision-containsInput",   "Contains Any"),
    ("field_PlattedLegalID_DOT_Subdivision",                 ""),
    ("field_PlattedLegalID_DOT_Lot",                         ""),
    ("field_PlattedLegalID_DOT_Block",                       ""),
    ("field_PlattedLegalID_DOT_Tract",                       ""),
    ("field_PLSSLegalID_DOT_QuarterSection-containsInput",   "Contains Any"),
    ("field_PLSSLegalID_DOT_QuarterSection",                 ""),
    ("field_PLSSLegalID_DOT_Section",                        ""),
    ("field_PLSSLegalID_DOT_Township",                       ""),
    ("field_PLSSLegalID_DOT_Range",                          ""),
    ("field_ParcelID",                                       ""),
]
for dt in doc_types:
    params.append(("field_selfservice_documentTypes-searchInput", dt))
params += [
    ("field_selfservice_documentTypes-containsInput",        "Contains Any"),
    ("field_selfservice_documentTypes",                      ""),
    ("field_UseAdvancedSearch",                              ""),
]

payload = urllib.parse.urlencode(params).encode("utf-8")
print("Payload length  :", len(payload))
print("Payload sample  :", payload[:200])
print()

r2 = s.post(
    f"{BASE}/web/searchPost/{SEARCH_ID}",
    data=payload,
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer":      f"{BASE}/web/search/{SEARCH_ID}",
        "Origin":       BASE,
        "X-Requested-With": "XMLHttpRequest",
    },
    timeout=30,
    allow_redirects=True,
)
print("Step2 status    :", r2.status_code)
print("Step2 final url :", r2.url)
print("Step2 C-Type    :", r2.headers.get("Content-Type", ""))
print("Step2 body      :", r2.text[:800])
print()
print("Cookies after POST:", dict(s.cookies))
print()

# ── Step 3: GET results ───────────────────────────────────────────────────────
time.sleep(1)
ts = int(time.time() * 1000)
r3 = s.get(
    f"{BASE}/web/searchResults/{SEARCH_ID}?page=1&_={ts}",
    headers={
        "Accept":            "*/*",
        "Referer":           f"{BASE}/web/search/{SEARCH_ID}",
        "X-Requested-With":  "XMLHttpRequest",
        "ajaxRequest":       "true",
    },
    timeout=30,
)
print("Step3 status    :", r3.status_code)
print("Step3 body[:2000]:")
print(r3.text[:2000])
