"""PDF → list of PIL Images via pypdfium2 (no system poppler needed).

pypdfium2 wraps PDFium, which is NOT thread-safe at the document level.
Concurrent renders from multiple threads can crash with SIGTRAP. We
serialize rendering with a module-level lock — render is fast (~200ms /
PDF) and the LLM call (~10s) is what we actually want to parallelize.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

_RENDER_LOCK = threading.Lock()


def render_pages(
    pdf_path: str | Path,
    pages: list[int] | None = None,
    dpi: int = 200,
) -> list[Image.Image]:
    """Render selected pages of `pdf_path` to RGB PIL Images at `dpi`.

    `pages` is 1-based (matches PDF page numbering). None = render all pages.
    """
    scale = dpi / 72.0
    images: list[Image.Image] = []
    with _RENDER_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            indices_1b = pages if pages is not None else list(range(1, len(pdf) + 1))
            for p in indices_1b:
                page = pdf[p - 1]
                bitmap = page.render(scale=scale)
                images.append(bitmap.to_pil().convert("RGB"))
        finally:
            pdf.close()
    return images
