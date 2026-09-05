from pathlib import Path

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
