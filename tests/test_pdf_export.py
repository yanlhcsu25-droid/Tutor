from calculus_agent.papers import pdf_export
from calculus_agent.schemas import PaperItemRead, PaperPreviewRead


def _paper() -> PaperPreviewRead:
    return PaperPreviewRead(
        title="数学公式测试卷",
        total_score=10,
        items=[
            PaperItemRead(
                question_id="q1",
                question_text=r"计算 $\frac{\sqrt{2}}{2}$。",
                question_type="解答题",
                score=10,
            )
        ],
        constraints=[],
        warnings=[],
        feasible=True,
    )


def test_pdf_export_falls_back_when_latex_engine_is_missing(monkeypatch):
    monkeypatch.setattr(pdf_export.shutil, "which", lambda _: None)
    monkeypatch.setattr(pdf_export, "_known_binary", lambda _: None)
    result = pdf_export.export_paper_pdf(_paper(), teacher_version=False)
    assert result.content.startswith(b"%PDF")
    assert result.renderer == "reportlab"
    assert result.warning


def test_pdf_export_uses_available_latex_engine(monkeypatch):
    monkeypatch.setattr(pdf_export, "_find_engine", lambda _: ("tectonic", "/bin/tectonic"))
    monkeypatch.setattr(pdf_export, "_compile_latex", lambda *args, **kwargs: b"%PDF-latex")
    result = pdf_export.export_paper_pdf(_paper(), teacher_version=False)
    assert result.content == b"%PDF-latex"
    assert result.renderer == "tectonic"
    assert result.warning is None


def test_pdf_export_falls_back_after_compiler_failure(monkeypatch):
    monkeypatch.setattr(pdf_export, "_find_engine", lambda _: ("xelatex", "/bin/xelatex"))

    def fail(*args, **kwargs):
        raise RuntimeError("formula compilation failed")

    monkeypatch.setattr(pdf_export, "_compile_latex", fail)
    result = pdf_export.export_paper_pdf(_paper(), teacher_version=False)
    assert result.content.startswith(b"%PDF")
    assert result.renderer == "reportlab"
    assert "formula compilation failed" in (result.warning or "")
