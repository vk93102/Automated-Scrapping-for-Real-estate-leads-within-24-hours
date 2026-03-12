from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from maricopa_scraper.http_client import RetryConfig, new_session, with_retry

logger = logging.getLogger(__name__)

_LOGIN_MARKERS = (
    "Public User Login",
    "You must be logged in to access the requested page",
    "Your session is no longer active",
)
_ACCOUNT_HREF_RE = re.compile(r"account\.jsp\?([^#]+)", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_MONEY_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{1,2})?)")
_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b")
_AREA_RE = re.compile(r"\b([\d,.]+\s*(?:acres?|acre|sq\.?\s*ft\.?|square\s+feet|sf))\b", re.IGNORECASE)


class ApacheAssessorError(RuntimeError):
    """Base error for Apache County Assessor scraping."""


class LoginRequiredError(ApacheAssessorError):
    """Raised when the site redirects to or serves the login page."""


@dataclass
class AccountLink:
    account_number: str
    doc: str
    href: str
    label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "accountNumber": self.account_number,
            "doc": self.doc,
            "requestUrl": self.href,
            "label": self.label,
        }


@dataclass
class AccountDocument:
    account_number: str
    doc: str
    source_url: str
    title: str
    fields: dict[str, str] = field(default_factory=dict)
    text_snippet: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "accountNumber": self.account_number,
            "doc": self.doc,
            "requestUrl": self.source_url,
            "title": self.title,
            "fields": self.fields,
            "textSnippet": self.text_snippet,
        }


@dataclass
class SearchRecord:
    account_number: str
    source_url: str
    row_index: int
    columns: dict[str, str] = field(default_factory=dict)
    row_text: str = ""
    links: list[AccountLink] = field(default_factory=list)
    documents: list[AccountDocument] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "accountNumber": self.account_number,
            "requestUrl": self.source_url,
            "rowIndex": self.row_index,
            "rowText": self.row_text,
            "columns": self.columns,
            "normalized": _normalize_record(self),
            "links": [x.as_dict() for x in self.links],
            "documents": [x.as_dict() for x in self.documents],
            "detailRequestUrls": [x.source_url for x in self.documents],
        }


@dataclass
class SearchRunResult:
    submitted_search_request_url: str = ""
    initial_results_request_url: str = ""
    final_results_request_url: str = ""
    search_id: str = ""
    page: int = 1
    page_size: int = 100
    sort: str = "Document Type"
    direction: str = "asc"
    records: list[SearchRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "county": "Apache",
            "officeName": "Apache County Assessor",
            "source": "Apache County Assessor",
            "submittedSearchRequestUrl": self.submitted_search_request_url,
            "initialResultsRequestUrl": self.initial_results_request_url,
            "finalResultsRequestUrl": self.final_results_request_url,
            "searchId": self.search_id,
            "page": self.page,
            "pageSize": self.page_size,
            "sort": self.sort,
            "dir": self.direction,
            "recordCount": len(self.records),
            "normalizedRecords": [_normalize_record(record) for record in self.records],
            "records": [record.as_dict() for record in self.records],
        }


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip())


def _normalize_key(value: str) -> str:
    return _slugify(value)


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", _normalize_text(value)).strip("_").lower()
    return s or "value"


def _looks_like_login_page(html: str) -> bool:
    text = html or ""
    return any(marker in text for marker in _LOGIN_MARKERS)


def _extract_search_id_from_url(url: str) -> str:
    try:
        parsed = parse_qs(urljoin("https://placeholder/", url).split("?", 1)[1], keep_blank_values=True)
    except Exception:
        parsed = parse_qs(url.split("?", 1)[1], keep_blank_values=True) if "?" in url else {}
    return (parsed.get("searchId") or [""])[0].strip()


def _looks_like_money(value: str) -> bool:
    return bool(_MONEY_RE.search(value or ""))


def _normalize_money(value: str) -> str:
    m = _MONEY_RE.search(value or "")
    return "" if not m else m.group(1).replace(",", "").strip()


def _normalize_date(value: str) -> str:
    m = _DATE_RE.search(value or "")
    return "" if not m else m.group(1).strip()


def _normalize_area(value: str) -> str:
    m = _AREA_RE.search(value or "")
    return "" if not m else _normalize_text(m.group(1))


def _collect_candidate_fields(record: "SearchRecord") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in record.columns.items():
        if value:
            out[_normalize_key(key)] = _normalize_text(value)
    for doc in record.documents:
        for key, value in doc.fields.items():
            if value:
                norm_key = _normalize_key(key)
                out.setdefault(norm_key, _normalize_text(value))
    return out


def _pick_value(candidates: dict[str, str], patterns: tuple[str, ...]) -> str:
    for key, value in candidates.items():
        if any(pattern in key for pattern in patterns) and value:
            return value
    return ""


def _pick_request_url(record: "SearchRecord") -> str:
    if record.documents:
        for doc in record.documents:
            lowered = (doc.doc or "").lower()
            if "salehist" in lowered or "doc" in lowered:
                return doc.source_url
        return record.documents[0].source_url
    if record.links:
        return record.links[0].href
    return record.source_url


def _normalize_record(record: "SearchRecord") -> dict[str, Any]:
    candidates = _collect_candidate_fields(record)

    owner_name = _pick_value(candidates, ("owner", "owner_name", "taxpayer"))
    grantee_name = _pick_value(candidates, ("grantee", "buyer", "recipient"))
    parcel_number = _pick_value(candidates, ("parcel", "parcel_number", "parcel_no", "apn"))
    property_address = _pick_value(candidates, ("property_address", "situs", "address", "site_address"))
    document_type = _pick_value(candidates, ("document_type", "doc_type", "instrument", "deed_type"))
    sale_date_raw = _pick_value(candidates, ("sale_date", "transfer_date", "recorded_date", "date"))
    sale_price_raw = _pick_value(candidates, ("sale_price", "price", "consideration", "amount", "saleamount"))
    area_raw = _pick_value(candidates, ("area", "acre", "acres", "square_feet", "sq_ft", "land_area"))

    if not sale_price_raw:
        for value in candidates.values():
            if _looks_like_money(value):
                sale_price_raw = value
                break

    if not area_raw:
        for value in candidates.values():
            maybe_area = _normalize_area(value)
            if maybe_area:
                area_raw = maybe_area
                break

    return {
        "accountNumber": record.account_number,
        "ownerName": owner_name,
        "granteeName": grantee_name,
        "salePrice": _normalize_money(sale_price_raw),
        "area": _normalize_area(area_raw) or _normalize_text(area_raw),
        "saleDate": _normalize_date(sale_date_raw),
        "parcelNumber": parcel_number,
        "propertyAddress": property_address,
        "documentType": document_type,
        "requestUrl": _pick_request_url(record),
    }


def _find_form_fields(form: Any) -> tuple[Optional[str], Optional[str]]:
    username_name: Optional[str] = None
    password_name: Optional[str] = None
    for inp in form.find_all("input"):
        name = (inp.get("name") or "").strip()
        input_type = (inp.get("type") or "text").strip().lower()
        if not name:
            continue
        lowered = name.lower()
        if input_type == "password" or "pass" in lowered:
            password_name = password_name or name
        if input_type in ("text", "email") or any(x in lowered for x in ("user", "login", "email")):
            username_name = username_name or name
    return username_name, password_name


def _extract_hidden_inputs(form: Any) -> dict[str, str]:
    payload: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = (inp.get("name") or "").strip()
        if not name:
            continue
        payload[name] = inp.get("value") or ""
    return payload


def _parse_account_href(base_url: str, href: str, label: str = "") -> Optional[AccountLink]:
    if not href:
        return None
    full_url = urljoin(base_url, href)
    m = _ACCOUNT_HREF_RE.search(full_url)
    if not m:
        return None
    qs = parse_qs(m.group(1), keep_blank_values=True)
    account_number = (qs.get("accountNum") or [""])[0].strip()
    doc = (qs.get("doc") or [""])[0].strip()
    if not account_number:
        return None
    return AccountLink(account_number=account_number, doc=doc, href=full_url, label=_normalize_text(label))


def _pick_results_table(soup: BeautifulSoup) -> Optional[Any]:
    best_table = None
    best_score = -1
    for table in soup.find_all("table"):
        score = 0
        for link in table.find_all("a", href=True):
            if _ACCOUNT_HREF_RE.search(link["href"]):
                score += 1
        if score > best_score:
            best_table = table
            best_score = score
    return best_table if best_score > 0 else None


def _extract_headers(table: Any) -> list[str]:
    headers: list[str] = []
    for th in table.find_all("th"):
        text = _normalize_text(th.get_text(" ", strip=True))
        if text:
            headers.append(text)
    return headers


def parse_results_page(html: str, *, source_url: str) -> list[SearchRecord]:
    if _looks_like_login_page(html):
        raise LoginRequiredError("The assessor site returned the login page while fetching results")

    soup = BeautifulSoup(html, "html.parser")
    table = _pick_results_table(soup)
    if table is None:
        return []

    headers = _extract_headers(table)
    records: list[SearchRecord] = []
    row_index = 0

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        row_links: list[AccountLink] = []
        for anchor in tr.find_all("a", href=True):
            link = _parse_account_href(source_url, anchor["href"], anchor.get_text(" ", strip=True))
            if link is not None:
                row_links.append(link)
        if not row_links:
            continue

        values = [_normalize_text(td.get_text(" ", strip=True)) for td in tds]
        columns: dict[str, str] = {}
        for idx, value in enumerate(values):
            raw_header = headers[idx] if idx < len(headers) else f"Column {idx + 1}"
            key = _slugify(raw_header)
            if key in columns:
                key = f"{key}_{idx + 1}"
            columns[key] = value

        primary = row_links[0]
        records.append(
            SearchRecord(
                account_number=primary.account_number,
                source_url=source_url,
                row_index=row_index,
                columns=columns,
                row_text=" | ".join(x for x in values if x),
                links=row_links,
            )
        )
        row_index += 1

    return records


def parse_account_page(
    html: str,
    *,
    source_url: str,
    account_number: str,
    doc: str,
) -> AccountDocument:
    if _looks_like_login_page(html):
        raise LoginRequiredError(f"Session expired while fetching account {account_number}")

    soup = BeautifulSoup(html, "html.parser")
    title_parts: list[str] = []
    for tag in soup.find_all(["title", "h1", "h2"]):
        text = _normalize_text(tag.get_text(" ", strip=True))
        if text and text not in title_parts:
            title_parts.append(text)
    title = " | ".join(title_parts[:3]) or f"Account {account_number}"

    fields: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) != 2:
            continue
        key = _normalize_text(cells[0].get_text(" ", strip=True)).rstrip(":")
        value = _normalize_text(cells[1].get_text(" ", strip=True))
        if not key or not value or len(key) > 120:
            continue
        fields.setdefault(key, value)

    text_snippet = _normalize_text(soup.get_text(" ", strip=True))[:1500]
    return AccountDocument(
        account_number=account_number,
        doc=doc,
        source_url=source_url,
        title=title,
        fields=fields,
        text_snippet=text_snippet,
    )


class ApacheAssessorClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 30.0,
        proxies: Optional[dict[str, str]] = None,
        retry: Optional[RetryConfig] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.proxies = proxies
        self.retry = retry or RetryConfig(attempts=3, base_sleep_s=1.0, max_sleep_s=8.0)
        self.session = session or new_session()
        self.session.headers.setdefault("Referer", f"{self.base_url}/assessor/")
        self._username: str = ""
        self._password: str = ""

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout_s)
        if self.proxies:
            kwargs.setdefault("proxies", self.proxies)

        def _do() -> requests.Response:
            return self.session.request(method, url, **kwargs)

        resp = with_retry(_do, cfg=self.retry)
        resp.raise_for_status()
        return resp

    def login(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        login_url = urljoin(self.base_url + "/", "assessor/web/login.jsp")
        resp = self._request("GET", login_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form")
        if form is None:
            raise ApacheAssessorError("Login form not found on the assessor login page")

        username_name, password_name = _find_form_fields(form)
        if not username_name or not password_name:
            raise ApacheAssessorError("Could not identify username/password fields on the assessor login form")

        payload = _extract_hidden_inputs(form)
        payload[username_name] = username
        payload[password_name] = password

        action = form.get("action") or login_url
        submit_url = urljoin(resp.url, action)
        post_resp = self._request("POST", submit_url, data=payload, allow_redirects=True)
        if _looks_like_login_page(post_resp.text):
            raise LoginRequiredError("Assessor login failed; verify credentials and account access")
        logger.info("Apache assessor login succeeded")

    def _relogin(self) -> None:
        if not self._username or not self._password:
            raise LoginRequiredError("Session expired and no stored credentials are available for re-login")
        logger.info("Apache assessor session expired; attempting re-login")
        self.login(self._username, self._password)

    def build_results_url(
        self,
        *,
        search_id: str,
        page: int,
        page_size: int,
        sort: str,
        direction: str,
    ) -> str:
        query = urlencode(
            {
                "searchId": search_id,
                "page": max(1, int(page)),
                "pageSize": max(1, int(page_size)),
                "sort": sort,
                "dir": direction,
            }
        )
        return f"{self.base_url}/assessor/taxweb/saleResults.jsp?{query}"

    def submit_sale_search(self, form_data: dict[str, str]) -> tuple[str, str, str]:
        post_url = f"{self.base_url}/assessor/taxweb/saleSearchPOST.jsp"
        resp = self._request("POST", post_url, data=form_data, allow_redirects=True)
        if _looks_like_login_page(resp.text):
            self._relogin()
            resp = self._request("POST", post_url, data=form_data, allow_redirects=True)
            if _looks_like_login_page(resp.text):
                raise LoginRequiredError("The assessor site redirected the sale search request back to login")
        final_url = str(resp.url)
        search_id = _extract_search_id_from_url(final_url)
        return resp.text, final_url, search_id

    def fetch_results_page(
        self,
        *,
        results_url: str,
    ) -> tuple[str, str]:
        resp = self._request("GET", results_url)
        if _looks_like_login_page(resp.text):
            self._relogin()
            resp = self._request("GET", results_url)
            if _looks_like_login_page(resp.text):
                raise LoginRequiredError("The assessor site returned the login page while fetching results")
        return resp.text, str(resp.url)

    def fetch_account_document(self, *, account_number: str, doc: str) -> AccountDocument:
        doc_url = (
            f"{self.base_url}/assessor/taxweb/account.jsp?"
            f"{urlencode({'accountNum': account_number, 'doc': doc})}"
        )
        resp = self._request("GET", doc_url)
        if _looks_like_login_page(resp.text):
            self._relogin()
            resp = self._request("GET", doc_url)
            if _looks_like_login_page(resp.text):
                raise LoginRequiredError(f"Session expired while fetching account {account_number}")
        return parse_account_page(resp.text, source_url=str(resp.url), account_number=account_number, doc=doc)

    def scrape_sale_results(
        self,
        *,
        results_url: Optional[str] = None,
        search_id: str = "",
        search_form_data: Optional[dict[str, str]] = None,
        page: int = 1,
        page_size: int = 100,
        sort: str = "Document Type",
        direction: str = "asc",
        max_pages: int = 1,
        max_records: int = 0,
        include_details: bool = True,
    ) -> SearchRunResult:
        if not results_url and not search_id and not search_form_data:
            raise ApacheAssessorError("results_url, search_id, or search_form_data is required")

        all_records: list[SearchRecord] = []
        seen_docs: set[tuple[str, str]] = set()
        pages_to_fetch = max(1, int(max_pages))
        current_page = max(1, int(page))
        run = SearchRunResult(page=current_page, page_size=page_size, sort=sort, direction=direction)

        first_page_html: Optional[str] = None
        if search_form_data:
            first_page_html, results_url, search_id = self.submit_sale_search(search_form_data)
            run.submitted_search_request_url = f"{self.base_url}/assessor/taxweb/saleSearchPOST.jsp"
            run.initial_results_request_url = results_url or ""
            run.final_results_request_url = results_url or ""
            run.search_id = search_id
        else:
            run.search_id = search_id
            if results_url:
                run.initial_results_request_url = results_url

        for page_offset in range(pages_to_fetch):
            url = results_url or self.build_results_url(
                search_id=search_id,
                page=current_page,
                page_size=page_size,
                sort=sort,
                direction=direction,
            )
            if page_offset == 0 and first_page_html is not None and results_url:
                html = first_page_html
                final_url = results_url
            else:
                html, final_url = self.fetch_results_page(results_url=url)
            run.final_results_request_url = final_url
            if not run.search_id:
                run.search_id = _extract_search_id_from_url(final_url)
            page_records = parse_results_page(html, source_url=final_url)
            if not page_records:
                break

            logger.info("Fetched %d sale result rows from %s", len(page_records), final_url)
            for record in page_records:
                if include_details:
                    for link in record.links:
                        key = (link.account_number, link.doc)
                        if key in seen_docs:
                            continue
                        seen_docs.add(key)
                        try:
                            record.documents.append(
                                self.fetch_account_document(account_number=link.account_number, doc=link.doc)
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to fetch account document account=%s doc=%s: %s",
                                link.account_number,
                                link.doc,
                                exc,
                            )
                all_records.append(record)
                if max_records and len(all_records) >= int(max_records):
                    run.records = all_records
                    return run

            if not search_id:
                break
            current_page += 1

        run.records = all_records
        return run
