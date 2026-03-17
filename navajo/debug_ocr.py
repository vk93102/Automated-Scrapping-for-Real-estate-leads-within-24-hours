#!/usr/bin/env python3
"""Diagnose OCR failure: truncated JPEG -> PNG re-encode -> Tesseract."""
import io
from pathlib import Path
from PIL import Image, ImageFile, ImageFilter, ImageEnhance

ImageFile.LOAD_TRUNCATED_IMAGES = True

import pytesseract
pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"

OUT = Path(__file__).resolve().parent / "output"

for dk in ["274473", "274474", "274475"]:
    img_path = OUT / f"debug_dk{dk}_pn1.jpg"
    if not img_path.exists():
        print(f"Missing: {img_path.name}")
        continue

    data = img_path.read_bytes()
    print(f"\n=== DK={dk} ===")
    print(f"  JPEG bytes: {len(data)}")
    print(f"  Last 2 bytes: {data[-2:].hex()} (valid JPEG ends with ffd9)")

    # Open with truncation tolerance
    im = Image.open(io.BytesIO(data))
    im.load()
    print(f"  PIL opened: size={im.size} mode={im.mode}")

    # Convert to RGB and re-encode as lossless PNG
    im_rgb = im.convert("RGB")
    buf = io.BytesIO()
    im_rgb.save(buf, format="PNG")
    buf.seek(0)
    im_png = Image.open(buf)
    print(f"  PNG re-encoded: size={im_png.size}")

    # Basic OCR
    text_raw = pytesseract.image_to_string(im_png, config="--psm 6 --oem 3")
    print(f"  Raw OCR chars: {len(text_raw)}")

    # Enhanced OCR: upscale 2x + sharpen + contrast
    w, h = im_rgb.size
    im_big = im_rgb.resize((w * 2, h * 2), Image.LANCZOS)
    im_sharp = im_big.filter(ImageFilter.SHARPEN)
    im_contrast = ImageEnhance.Contrast(im_sharp).enhance(1.8)
    text_enh = pytesseract.image_to_string(im_contrast, config="--psm 6 --oem 3")
    print(f"  Enhanced OCR chars: {len(text_enh)}")
    print(f"  Enhanced OCR preview:\n---\n{text_enh[:1200]}\n---")

    # Save enhanced image for visual inspection
    enh_path = OUT / f"debug_dk{dk}_pn1_enhanced.png"
    im_contrast.save(enh_path)
    print(f"  Enhanced image saved: {enh_path.name}")
