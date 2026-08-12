from io import BytesIO

import numpy as np
from reportlab.pdfgen import canvas

from calculus_agent.ocr.pdf_blank_lines import (
    PdfHorizontalLine,
    extract_pdf_horizontal_lines,
    restore_vector_blanks,
)


def _result(*blocks):
    return {
        "page_width": 600,
        "page_height": 800,
        "ocr_result": {
            "rec_texts": [text for text, _ in blocks],
            "rec_boxes": [box for _, box in blocks],
        }
    }


def test_extracts_short_horizontal_vector_but_not_separator_or_table(tmp_path):
    path = tmp_path / "blanks.pdf"
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800))
    pdf.line(180, 650, 260, 650)  # answer rule
    pdf.line(50, 500, 550, 500)   # page separator
    pdf.rect(100, 300, 100, 30)   # table/box edges
    pdf.save()
    path.write_bytes(buffer.getvalue())

    lines = extract_pdf_horizontal_lines(path)[1]
    assert len(lines) == 1
    assert abs(lines[0].x1 - 180) <= 1
    assert abs(lines[0].x2 - 260) <= 1


def test_restores_blank_after_same_row_ocr_text():
    lines = [PdfHorizontalLine(180, 150, 260, 600, 800)]
    result = _result(("若函数连续，则a=", [40, 140, 175, 165]))
    assert restore_vector_blanks("1. 若函数连续，则a=", result, lines) == "1. 若函数连续，则a=____"


def test_restores_blank_between_two_text_blocks():
    lines = [PdfHorizontalLine(180, 150, 250, 600, 800)]
    result = _result(
        ("若a=", [40, 140, 175, 165]),
        ("，则函数连续", [260, 140, 380, 165]),
    )
    assert restore_vector_blanks("1. 若a=，则函数连续", result, lines) == "1. 若a=____，则函数连续"


def test_does_not_restore_without_same_row_text_anchor():
    lines = [PdfHorizontalLine(180, 300, 260, 600, 800)]
    result = _result(("若函数连续，则a=", [40, 140, 175, 165]))
    assert restore_vector_blanks("1. 若函数连续，则a=", result, lines).endswith("a=")


def test_does_not_restore_long_rule_or_invent_from_fill_blank_text():
    result = _result(("若函数连续，则a=", [40, 140, 175, 165]))
    assert restore_vector_blanks("1. 若函数连续，则a=", result, []) == "1. 若函数连续，则a="


def test_falls_back_to_page_image_line_and_records_diagnostics():
    image = np.full((800, 600, 3), 255, dtype=np.uint8)
    image[150:152, 180:251] = 0
    result = _result(("若函数连续，则a=", [40, 140, 175, 165]))
    result["doc_preprocessor_res"] = {"output_img": image}
    diagnostics = {}

    restored = restore_vector_blanks("1. 若函数连续，则a=", result, [], diagnostics)

    assert restored == "1. 若函数连续，则a=____"
    assert diagnostics["image_line_detection"]["used"] is True
    assert diagnostics["candidate_blank_lines"][0]["source"] == "page_image"
    assert diagnostics["ocr_text_boxes"]
    assert diagnostics["same_row_matches"][0]["accepted"] is True
    assert diagnostics["restored_blank_lines"]


def test_diagnostics_record_rejected_pdf_horizontal_lines(tmp_path):
    path = tmp_path / "separator.pdf"
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(600, 800))
    pdf.line(50, 500, 550, 500)
    pdf.save()
    path.write_bytes(buffer.getvalue())
    diagnostics = {}

    assert extract_pdf_horizontal_lines(path, diagnostics) == {}
    raw = diagnostics[1]["raw_pdf_horizontal_lines"]
    assert raw[0]["reason"] == "too_long_or_page_separator"
