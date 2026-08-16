from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

import calculus_agent.ocr.mineru_adapter as mineru_adapter
from calculus_agent.ocr.mineru_adapter import (
    MinerUCancelled,
    _read_mineru_progress,
    content_blocks_to_pages,
    prepare_selected_pdf,
)


def test_read_mineru_progress_parses_latest_tqdm_stage(tmp_path):
    log = tmp_path / "mineru.log"
    log.write_text(
        "Layout Predict: 100%|████| 20/20 [00:03<00:00]\r"
        "Predict:  68%|██▋| 300/439 [07:12<03:20]\r",
        encoding="utf-8",
    )

    position, tail, progress = _read_mineru_progress(log, 0, "")

    assert position == log.stat().st_size
    assert tail
    assert progress == (300, 439, "mineru_predict")


def test_read_mineru_progress_supports_ocr_and_page_output(tmp_path):
    log = tmp_path / "mineru.log"
    log.write_text("OCR-det:  50%|██| 139/278 [00:09<00:08]\r", encoding="utf-8")
    position, tail, progress = _read_mineru_progress(log, 0, "")
    assert progress == (139, 278, "mineru_ocr")

    with log.open("a", encoding="utf-8") as stream:
        stream.write("Processing pages:  60%|██| 12/20 [00:00<00:00]\r")
    _, _, progress = _read_mineru_progress(log, position, tail)
    assert progress == (12, 20, "mineru_pages")


def _three_page_pdf(path: Path) -> None:
    images = [Image.new("RGB", (100, 140), color) for color in ("red", "green", "blue")]
    images[0].save(path, format="PDF", save_all=True, append_images=images[1:])
    for image in images:
        image.close()


def test_prepare_selected_pdf_is_lossless_subset_with_original_page_map(tmp_path):
    source = tmp_path / "source.pdf"
    target = tmp_path / "selected.pdf"
    _three_page_pdf(source)

    result_path, page_map = prepare_selected_pdf(source, target, [1, 3])

    assert result_path == target
    assert page_map == (1, 3)
    document = pdfium.PdfDocument(str(target))
    assert len(document) == 2
    document.close()


def test_content_blocks_to_pages_preserves_original_pages_and_math():
    blocks = [
        {"type": "header", "text": "公众号", "page_idx": 0},
        {"type": "text", "text": "选择题", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "1. 求 $\\lim_{x\\to0}f(x)$", "page_idx": 0},
        {"type": "equation", "text": "$$x^2$$", "page_idx": 0},
        {"type": "text", "text": "1. 解：答案", "page_idx": 1},
        {"type": "page_number", "text": "1", "page_idx": 1},
    ]

    pages = content_blocks_to_pages(blocks, [2, 9])

    assert pages == [
        (2, "## 选择题\n\n1. 求 $\\lim_{x\\to0}f(x)$\n\n$$x^2$$\n"),
        (9, "1. 解：答案\n"),
    ]


def test_run_mineru_terminates_process_tree_when_cancelled(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 123
        returncode = None

        def poll(self):
            return self.returncode

    process = FakeProcess()
    terminated = []
    monkeypatch.setattr(mineru_adapter, "resolve_mineru_binary", lambda: Path("/bin/echo"))
    monkeypatch.setattr(mineru_adapter.subprocess, "Popen", lambda *args, **kwargs: process)

    def terminate(item):
        terminated.append(item.pid)
        item.returncode = -15

    monkeypatch.setattr(mineru_adapter, "_terminate_process_tree", terminate)

    with pytest.raises(MinerUCancelled):
        mineru_adapter.run_mineru(
            tmp_path / "input.pdf",
            tmp_path / "output",
            cancel_callback=lambda: True,
        )
    assert terminated == [123]
