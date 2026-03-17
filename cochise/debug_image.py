#!/usr/bin/env python3
"""Diagnostic: test image download + OCR from saved session cookies."""
import io
import json
import sys
from pathlib import Path

import requests
from PIL import Image

# Ensure tesseract path
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"
    _TESS_OK = True
except Exception as e:
    print(f"pytesseract import error: {e}")
    _TESS_OK = False

ROOT = Path(__file__).resolve().parent
OUT  = ROOT / "output"

state_path = OUT / "session_state.json"
cookies = json.loads(state_path.read_text())["cookies"]
print(f"Loaded {len(cookies)} cookies from session_state.json")

# Build requests session with every cookie
s = requests.Session()
s.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.thecountyrecorder.com/Document.aspx",
})
for c in cookies:
    domain = c.get("domain", "").lstrip(".")
    s.cookies.set(c["name"], c["value"], domain=domain, path=c.get("path", "/"))

print("Cookies set:", {c["name"]: c["value"][:20] + "..." for c in cookies})

BASE = "https://www.thecountyrecorder.com"

# Try a few DKs from recent output
DKS_TO_TEST = ["274473", "274474", "274475"]

for dk in DKS_TO_TEST:
    print(f"\n=== DK={dk} ===")
    # First fetch the detail page to warm up the session for this DK
    det_url = f"{BASE}/Document.aspx?DK={dk}"
    dr = s.get(det_url, timeout=20)
    print(f"  Detail page: {dr.status_code} bytes={len(dr.content)}")

    for pn in range(1, 5):
        url = f"{BASE}/ImageHandler.ashx?DK={dk}&PN={pn}"
        try:
            r = s.get(url, timeout=20)
            ctype = r.headers.get("Content-Type", "")
            nbytes = len(r.content)
            print(f"  PN={pn}: status={r.status_code} ctype={ctype!r} bytes={nbytes}")

            if r.status_code == 200 and "image" in ctype.lower() and nbytes > 500:
                img_path = OUT / f"debug_dk{dk}_pn{pn}.jpg"
                img_path.write_bytes(r.content)
                print(f"  Saved: {img_path.name}")

                if _TESS_OK:
                    im = Image.open(io.BytesIO(r.content))
                    print(f"  Image size={im.size} mode={im.mode}")

                    # Raw OCR
                    raw = pytesseract.image_to_string(im)
                    print(f"  Raw OCR chars: {len(raw)}")
                    print(f"  Raw OCR preview:\n---\n{raw[:600]}\n---")

                    # Improved: grayscale + upscale + sharpen
                    from PIL import ImageFilter, ImageEnhance
                    im_gray = im.convert("L")
                    # Upscale 2x for better OCR
                    w, h = im_gray.size
                    im_big = im_gray.resize((w * 2, h * 2), Image.LANCZOS)
                    im_sharp = im_big.filter(ImageFilter.SHARPEN)
                    im_contrast = ImageEnhance.Contrast(im_sharp).enhance(2.0)

                    enhanced = pytesseract.image_to_string(
                        im_contrast,
                        config="--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz$.,/:;-() "
                    )
                    print(f"  Enhanced OCR chars: {len(enhanced)}")
                    print(f"  Enhanced OCR preview:\n---\n{enhanced[:600]}\n---")

                    # Full unrestricted OCR
                    full = pytesseract.image_to_string(im_contrast, config="--psm 6 --oem 3")
                    print(f"  Full enhanced OCR chars: {len(full)}")
                    print(f"  Full enhanced OCR preview:\n---\n{full[:800]}\n---")
            elif nbytes == 0 or nbytes < 100:
                print(f"  -> Empty/tiny response, stopping page probe for DK={dk}")
                break
        except Exception as e:
            print(f"  PN={pn} error: {e}")
            break

print("\nDone. Check lapaz/output/ for saved images.")
