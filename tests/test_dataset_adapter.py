import json
from pathlib import Path

from sqlalchemy import select

from calculus_agent.datasets.ugmathbench import import_ugmathbench
from calculus_agent.models import QuestionDraft


def test_imports_selected_variants_without_duplicates(session, tmp_path: Path) -> None:
    path = tmp_path / "calculus.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "calc-1",
                    "subject": "Calculus_-_single_variable",
                    "topic": "Limits and continuity",
                    "subtopic": "Basic limits",
                    "level": "2",
                    "keywords": ["limits"],
                    "problem_v1": "Evaluate lim x->0 sin(x)/x",
                    "answer_v1": ["1"],
                    "answer_type_v1": ["NV"],
                    "options_v1": [[]],
                    "problem_v2": "Evaluate lim x->0 sin(2x)/x",
                    "answer_v2": ["2"],
                    "answer_type_v2": ["NV"],
                    "options_v2": [[]],
                }
            ]
        ),
        encoding="utf-8",
    )
    first = import_ugmathbench(session, path, variants=[1, 2])
    second = import_ugmathbench(session, path, variants=[1, 2])

    assert first.created == 2
    assert second.existing == 2
    drafts = session.scalars(select(QuestionDraft)).all()
    assert [item.variant for item in drafts] == [1, 2]
