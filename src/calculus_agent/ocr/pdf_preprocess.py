"""Prepare PDF input for OCR without changing the source document."""

from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


DEFAULT_DPI = 180
FALLBACK_DPI = 220
MAX_LONG_EDGE = 2800
MAX_PIXELS = 6_000_000
JPEG_QUALITY = 90


@dataclass(frozen=True)
class PreparedPdf:
    path: Path
    metadata: dict[str, Any]
    page_images: tuple[Path, ...] = ()
    page_numbers: tuple[int, ...] = ()


def _render_scale(width_pt: float, height_pt: float, *, dpi: int = DEFAULT_DPI) -> float:
    """Calculate a bounded, non-enlarging render scale."""
    width = max(float(width_pt), 1.0)
    height = max(float(height_pt), 1.0)
    preferred = dpi / 72.0
    edge = MAX_LONG_EDGE / max(width, height)
    pixels = math.sqrt(MAX_PIXELS / (width * height))
    return min(preferred, edge, pixels)


def prepare_pdf_for_ocr(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    page_numbers: list[int] | tuple[int, ...] | None = None,
    dpi: int = DEFAULT_DPI,
) -> PreparedPdf:
    """Rasterize a PDF into an OCR-safe PDF and return its diagnostics.

    The source PDF is never modified.  The OCR copy keeps page aspect ratios,
    does not rotate or crop pages, and uses JPEG quality 90 inside the output
    PDF.  All OCR engines should receive the returned ``path``.
    """
    source = Path(pdf_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    target_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="ocr-pdf-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}.ocr.pdf"

    pages: list[Any] = []
    page_images: list[Path] = []
    page_dimensions: list[dict[str, Any]] = []
    document = pdfium.PdfDocument(str(source))
    source_page_count = len(document)
    try:
        available = set(range(1, source_page_count + 1))
        selected = list(page_numbers) if page_numbers is not None else sorted(available)
        if not selected:
            raise ValueError("至少需要选择一个 PDF 页面")
        invalid = set(selected) - available
        if invalid:
            raise ValueError(f"页码超出 PDF 范围：{sorted(invalid)}")
        if len(selected) != len(set(selected)):
            raise ValueError("PDF 页码不能重复")

        for page_number in selected:
            page = document[page_number - 1]
            width_pt, height_pt = page.get_size()
            scale = _render_scale(width_pt, height_pt, dpi=dpi)
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            pages.append(image)
            image_path = target_dir / f"page_{page_number:04d}.jpg"
            image.save(image_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            page_images.append(image_path)
            page_dimensions.append({
                "page": page_number,
                "original_pt": [width_pt, height_pt],
                "processed_px": list(image.size),
                "scale": scale,
            })
            del bitmap, page
    finally:
        document.close()

    if not pages:
        raise ValueError(f"PDF 没有页面：{source}")
    first, rest = pages[0], pages[1:]
    first.save(
        target,
        format="PDF",
        resolution=dpi,
        quality=JPEG_QUALITY,
        optimize=True,
        save_all=True,
        append_images=rest,
    )
    for image in pages:
        image.close()

    source_size = source.stat().st_size
    processed_size = target.stat().st_size
    metadata = {
        "source_path": str(source),
        "source_size_bytes": source_size,
        "processed_path": str(target),
        "processed_size_bytes": processed_size,
        "source_page_count": source_page_count,
        "page_count": len(page_dimensions),
        "selected_pages": selected,
        "target_dpi": dpi,
        "max_long_edge_px": MAX_LONG_EDGE,
        "max_pixels": MAX_PIXELS,
        "jpeg_quality": JPEG_QUALITY,
        "pages": page_dimensions,
        "compressed": processed_size != source_size,
    }
    return PreparedPdf(
        path=target,
        metadata=metadata,
        page_images=tuple(page_images),
        page_numbers=tuple(selected),
    )


def render_pdf_page(
    pdf_path: str | Path,
    page_number: int,
    output_path: str | Path,
    *,
    dpi: int = FALLBACK_DPI,
) -> Path:
    """Render one original PDF page for an OCR retry."""
    source = Path(pdf_path)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(source))
    try:
        if page_number < 1 or page_number > len(document):
            raise ValueError(f"页码超出 PDF 范围：{page_number}")
        page = document[page_number - 1]
        width_pt, height_pt = page.get_size()
        bitmap = page.render(scale=_render_scale(width_pt, height_pt, dpi=dpi))
        image = bitmap.to_pil().convert("RGB")
        image.save(target, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        image.close()
        del bitmap, page
    finally:
        document.close()
    return target


def format_prepare_warning(metadata: dict[str, Any]) -> str:
    """Compact JSON form suitable for existing OCR warning/diagnostic fields."""
    return "pdf_preprocess=" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
