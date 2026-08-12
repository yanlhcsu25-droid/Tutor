from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from calculus_agent.ocr.pdf_preprocess import (
    MAX_LONG_EDGE,
    MAX_PIXELS,
    prepare_pdf_for_ocr,
)


def _pdf(path: Path, width: float, height: float) -> None:
    image = Image.new("RGB", (100, 140), "white")
    image.save(path, format="PDF", resolution=72)


def test_prepare_pdf_for_ocr_records_bounded_dimensions_without_enlarging(tmp_path):
    source = tmp_path / "source.pdf"
    _pdf(source, 100, 140)

    prepared = prepare_pdf_for_ocr(source, tmp_path / "prepared")

    assert prepared.path.is_file()
    assert prepared.metadata["source_size_bytes"] == source.stat().st_size
    assert prepared.metadata["processed_size_bytes"] == prepared.path.stat().st_size
    assert prepared.metadata["page_count"] == 1
    page = prepared.metadata["pages"][0]
    assert max(page["processed_px"]) <= MAX_LONG_EDGE
    assert page["processed_px"][0] * page["processed_px"][1] <= MAX_PIXELS

    document = pdfium.PdfDocument(str(prepared.path))
    assert len(document) == 1
    document.close()
