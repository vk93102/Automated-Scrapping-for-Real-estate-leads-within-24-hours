from greenlee import extractor as ex
from playwright.sync_api import sync_playwright


def main() -> None:
    dk = "1403299"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        ex.COUNTY_LABEL = "COCHISE"
        ex.COUNTY_DISPLAY = "Cochise"
        ex._goto_document_search(page, verbose=False)
        cookies = ctx.cookies()
        browser.close()

    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    session = ex._make_session(cookie_header)
    detail = ex.fetch_detail(dk, session)
    image_urls = ex.discover_image_urls(dk, session, detail.get("imageUrls", []), max_probe_pages=4)
    ocr_text, ocr_method = ex.ocr_document_images(image_urls, session, max_pages=2)
    merged = (detail.get("rawText", "") + "\n" + ocr_text).strip()

    print("cookies", len(cookies))
    print("image_urls", image_urls)
    print("ocr", ocr_method, len(ocr_text))
    print("principal", ex._regex_principal(merged))
    print("address", ex._regex_address(merged))
    print("imageAccessNote", detail.get("imageAccessNote"))
    print("---OCR SAMPLE---")
    print(ocr_text[:2500])


if __name__ == "__main__":
    main()
