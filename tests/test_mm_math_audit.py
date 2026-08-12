import json

from calculus_agent.datasets.mm_math_audit import audit_mm_math, render_markdown


def test_audit_reports_language_and_official_encodings(tmp_path):
    path = tmp_path / "MM_Math.jsonl"
    rows = [
        {
            "question": "As shown in the figure, find x.",
            "solution": r"x=\boxed{2}",
            "difficult": "easy",
            "year": "seven",
            "knowledge": {"level_1": "Functions", "level_2": "Linear Function"},
            "file_name": "1.png",
        },
        {
            "question": "求 x。",
            "solution": "答案：3。",
            "difficult": "medium",
            "year": "eight",
            "knowledge": {"level_1": "Functions", "level_2": "Linear Function"},
            "file_name": "2.png",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )

    report = audit_mm_math(path)

    assert report["rows"] == 2
    assert report["language"]["questions_with_cjk"] == 1
    assert report["multimodal"]["likely_image_dependent_questions"] == 1
    assert report["difficulty_distribution"] == {"easy": 1, "medium": 1}
    assert "不能未经本地化" in render_markdown(report)
