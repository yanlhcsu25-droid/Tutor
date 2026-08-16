from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from calculus_agent.ocr.pdf_preprocess import (
    DEFAULT_DPI,
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


def test_prepare_pdf_for_ocr_only_renders_selected_original_pages(tmp_path):
    source = tmp_path / "source.pdf"
    images = [Image.new("RGB", (100, 140), color) for color in ("red", "green", "blue")]
    images[0].save(
        source,
        format="PDF",
        resolution=72,
        save_all=True,
        append_images=images[1:],
    )
    for image in images:
        image.close()

    prepared = prepare_pdf_for_ocr(
        source,
        tmp_path / "prepared",
        page_numbers=[1, 3],
    )

    assert prepared.page_numbers == (1, 3)
    assert [path.name for path in prepared.page_images] == ["page_0001.jpg", "page_0003.jpg"]
    assert all(path.is_file() for path in prepared.page_images)
    assert prepared.metadata["source_page_count"] == 3
    assert prepared.metadata["selected_pages"] == [1, 3]
    assert prepared.metadata["target_dpi"] == DEFAULT_DPI
    document = pdfium.PdfDocument(str(prepared.path))
    assert len(document) == 2
    document.close()


def test_prepare_pdf_for_ocr_rejects_out_of_range_page(tmp_path):
    source = tmp_path / "source.pdf"
    _pdf(source, 100, 140)

    try:
        prepare_pdf_for_ocr(source, tmp_path / "prepared", page_numbers=[2])
    except ValueError as error:
        assert "页码超出" in str(error)
    else:
        raise AssertionError("out-of-range page should fail")
