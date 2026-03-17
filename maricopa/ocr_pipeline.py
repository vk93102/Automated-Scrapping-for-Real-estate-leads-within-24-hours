from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _tmpdir() -> str:
    """Return a consistent temp dir for both pdf2image and pytesseract.

    On macOS, /tmp is a symlink → /private/tmp.  pytesseract writes its
    PPM temp file via Python's tempfile (respects TMPDIR), but the tesseract
    *binary* can resolve the symlink differently, causing “file not found”.
    Forcing both to the same real path (via TMPDIR env-var or /private/tmp
    fallback) fixes it.
    """
    td = os.environ.get("TMPDIR") or tempfile.gettempdir()
    # Resolve any symlinks so the path tesseract sees == path Python sees
    return str(Path(td).resolve())


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

    td = _tmpdir()
    images = convert_from_path(str(p), dpi=dpi, output_folder=td)
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
    """OCR a PDF provided as bytes (no need to write a temporary file).

    Resilience features:
    - Sets PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True so partially-downloaded
      or truncated images are processed instead of raising an exception.
    - Falls back to pdftocairo renderer if the default pdftoppm fails (handles
      PDFs with non-standard resolution metadata that cause Poppler exit -2).
    """

    if not pdf_bytes:
        return ""

    from pdf2image import convert_from_bytes
    import pytesseract
    from PIL import ImageFile

    # Allow Pillow to process truncated/incomplete image data without raising.
    ImageFile.LOAD_TRUNCATED_IMAGES = True

    td = _tmpdir()

    # Try default pdftoppm renderer first; fall back to pdftocairo on any
    # Poppler error (e.g. exit code -2 "Estimating resolution as …").
    try:
        images = convert_from_bytes(pdf_bytes, dpi=dpi, output_folder=td)
    except Exception:
        images = convert_from_bytes(
            pdf_bytes, dpi=dpi, output_folder=td, use_pdftocairo=True
        )

    if max_pages and max_pages > 0:
        images = images[:max_pages]

    chunks: list[str] = []
    for img in images:
        txt = pytesseract.image_to_string(img, lang=lang)
        chunks.append(txt or "")
    return "\n\n".join(chunks).strip()

