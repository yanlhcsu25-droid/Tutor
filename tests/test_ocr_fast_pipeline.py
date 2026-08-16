import sys
import types
from pathlib import Path

from calculus_agent.ocr.pdf_preprocess import PreparedPdf
from calculus_agent.workbench import ocr as ocr_module
from calculus_agent.workbench.import_pipeline import DocumentLayout


class _FakeResult:
    def __init__(self, number: int) -> None:
        self.number = number

    def save_to_markdown(self, path: Path) -> None:
        path.write_text(f"{self.number}. 测试题目\n", encoding="utf-8")


class _FakeDatabase:
    def __init__(self) -> None:
        self.pages: list[tuple[int, str]] = []

    def upsert_page(
        self, source_file_id: str, page_number: int, markdown: str, *, reset_edited: bool
    ) -> None:
        assert source_file_id == "src_test"
        assert reset_edited is True
        self.pages.append((page_number, markdown))


def test_fast_ppstructure_reuses_one_model_and_preserves_original_page_numbers(
    tmp_path, monkeypatch
):
    images = []
    for page_number in (2, 5):
        path = tmp_path / f"page_{page_number:04d}.jpg"
        path.write_bytes(b"fake-image")
        images.append(path)

    prepared = PreparedPdf(
        path=tmp_path / "prepared.pdf",
        metadata={"target_dpi": 180, "source_path": str(tmp_path / "source.pdf")},
        page_images=tuple(images),
        page_numbers=(2, 5),
    )
    constructor_calls: list[dict[str, object]] = []
    predict_inputs: list[str] = []

    class FakePPStructureV3:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

        def predict(self, *, input: str):
            predict_inputs.append(input)
            yield _FakeResult(len(predict_inputs))

    fake_paddleocr = types.ModuleType("paddleocr")
    fake_paddleocr.PPStructureV3 = FakePPStructureV3
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setattr(ocr_module, "_persist_candidate", lambda *args, **kwargs: 1)

    database = _FakeDatabase()
    metrics = {}
    progress = []
    result = ocr_module._run_unified_ppstructure_into_database(
        prepared,
        "src_test",
        database,
        device="cpu",
        raw_root=tmp_path / "raw",
        layout=None,
        diagnostics_out=None,
        progress_callback=lambda current, total, status: progress.append(
            (current, total, status)
        ),
        cancel_callback=None,
        metrics=metrics,
    )

    assert result == (2, 2)
    assert len(constructor_calls) == 1
    assert constructor_calls[0]["use_table_recognition"] is False
    assert constructor_calls[0]["use_doc_unwarping"] is False
    assert predict_inputs == [str(images[0]), str(images[1])]
    assert [page for page, _ in database.pages] == [2, 5]
    assert [item["page"] for item in metrics["pages"]] == [2, 5]
    assert (1, 2, "ocr_page_complete") in progress
    assert (2, 2, "ocr_page_complete") in progress


def test_mineru_pipeline_persists_original_page_numbers_and_uses_shared_matcher(
    tmp_path, monkeypatch
):
    blocks = [
        {"type": "text", "text": "1. 求极限", "page_idx": 0},
        {"type": "text", "text": "1. 答案：1", "page_idx": 1},
    ]
    monkeypatch.setattr(
        ocr_module,
        "run_mineru",
        lambda *args, **kwargs: (blocks, {"elapsed_seconds": 1.0}),
    )
    monkeypatch.setattr(ocr_module, "_persist_candidate", lambda *args, **kwargs: 1)
    database = _FakeDatabase()
    metrics = {}
    progress = []

    result = ocr_module._run_mineru_into_database(
        tmp_path / "selected.pdf",
        (2, 9),
        "src_test",
        database,
        raw_root=tmp_path / "raw",
        layout=DocumentLayout("separate", [2], [9]),
        diagnostics_out=[],
        progress_callback=lambda current, total, status: progress.append(
            (current, total, status)
        ),
        cancel_callback=None,
        metrics=metrics,
    )

    assert result == (2, 1)
    assert [page for page, _ in database.pages] == [2, 9]
    assert (1, 2, "ocr_page_complete") in progress
    assert (2, 2, "ocr_page_complete") in progress
    assert metrics["mineru"]["elapsed_seconds"] == 1.0
