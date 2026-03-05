from __future__ import annotations

from pathlib import Path


def ocr_pdf_to_text(
    pdf_path: str | Path,
    *,
    dpi: int = 300,
    lang: str = "eng",
    max_pages: int = 0,
) -> str:
    """Convert a PDF to images and OCR them with Tesseract.

    Requirements:
    - system: `tesseract` binary
    - system: `poppler` (for pdf2image)
    """

    # Local imports so the rest of the pipeline can run without OCR deps.
    from pdf2image import convert_from_path
    import pytesseract

    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    images = convert_from_path(str(p), dpi=dpi)
    if max_pages and max_pages > 0:
        images = images[:max_pages]

    chunks: list[str] = []
    for img in images:
        txt = pytesseract.image_to_string(img, lang=lang)
        chunks.append(txt or "")
    return "\n\n".join(chunks).strip()


def ocr_pdf_bytes_to_text(
    pdf_bytes: bytes,
    *,
    dpi: int = 300,
    lang: str = "eng",
    max_pages: int = 0,
) -> str:
    """OCR a PDF provided as bytes (no need to write a temporary file)."""

    if not pdf_bytes:
        return ""

    from pdf2image import convert_from_bytes
    import pytesseract

    images = convert_from_bytes(pdf_bytes, dpi=dpi)
    if max_pages and max_pages > 0:
        images = images[:max_pages]

    chunks: list[str] = []
    for img in images:
        txt = pytesseract.image_to_string(img, lang=lang)
        chunks.append(txt or "")
    return "\n\n".join(chunks).strip()

