import json

from sqlalchemy import func, select

from calculus_agent.datasets.cmm_math import import_cmm_math
from calculus_agent.models import KnowledgeNode, Question, QuestionDraft


def test_imports_text_only_middle_school_records(session, tmp_path):
    path = tmp_path / "all_data.jsonl"
    rows = [
        {
            "id": "1",
            "image": [],
            "answer": "B",
            "solution": "null",
            "level": "八年级",
            "question": "一次函数的图象经过哪个点？",
            "options": "A. $(0,0)$\nB. $(1,2)$",
            "subject": "解析几何",
            "analysis": "代入验证可知选 B。",
        },
        {
            "id": "2",
            "image": ["2.jpg"],
            "answer": "3",
            "solution": "null",
            "level": "八年级",
            "question": "如图求值。",
            "options": [],
            "subject": "代数",
            "analysis": "计算得 3。<ImageHere>",
        },
        {
            "id": "3",
            "image": [],
            "answer": "4",
            "solution": "null",
            "level": "高一",
            "question": "高中题。",
            "options": [],
            "subject": "代数",
            "analysis": "略。",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )

    summary = import_cmm_math(session, path)

    assert summary.created == 1
    assert summary.skipped == 1
    draft = session.scalar(select(QuestionDraft))
    assert draft.grade == "八年级"
    assert draft.question_type == "选择题"
    assert draft.options_json == ["A. $(0,0)$", "B. $(1,2)$"]
    assert "B. $(1,2)$" in draft.question_text
    assert session.scalar(select(Question)).final_answer == "B"
    assert session.scalar(select(KnowledgeNode)).name == "解析几何"


def test_skips_missing_analysis_by_default(session, tmp_path):
    path = tmp_path / "all_data.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "1",
                "image": [],
                "answer": "2",
                "solution": "null",
                "level": "七年级",
                "question": "计算 $1+1$。",
                "options": [],
                "subject": "算术",
                "analysis": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = import_cmm_math(session, path)

    assert summary.skipped == 1
    assert session.scalar(select(func.count()).select_from(Question)) == 0
