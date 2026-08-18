from types import SimpleNamespace

from calculus_agent.generation_diagnosis import SelectionEvidence
from calculus_agent.papers.selector import _build_selection_evidence


def _row(
    question_id: str,
    question_type: str,
    knowledge: list[str],
    *,
    has_image: bool = False,
):
    question = SimpleNamespace(
        id=question_id,
        question_type=question_type,
    )
    return (
        question,
        knowledge,
        has_image,
        None,
        None,
        None,
    )


def test_selection_evidence_comes_from_candidate_rows_not_preview_items():
    rows = [
        _row("q1", "计算题", ["极限"]),
        _row("q2", "计算题", ["极限", "连续"]),
        _row("q3", "证明题", ["连续"], has_image=True),
    ]

    evidence = _build_selection_evidence(
        rows=rows,
        eligible=rows,
        missing_required=["required-q"],
        solver_status="precheck_failed",
    )

    assert isinstance(evidence, SelectionEvidence)
    assert evidence.candidate_count == 3
    assert evidence.eligible_count == 3
    assert evidence.type_supply == {
        "计算题": 2,
        "证明题": 1,
    }
    assert evidence.knowledge_supply == {
        "极限": 2,
        "连续": 2,
    }
    assert evidence.image_supply == 1
    assert evidence.missing_required_question_ids == ["required-q"]
    assert evidence.solver_status == "precheck_failed"


def test_duplicate_knowledge_link_on_one_question_does_not_inflate_supply():
    rows = [
        _row("q1", "计算题", ["极限", "极限"]),
    ]

    evidence = _build_selection_evidence(
        rows=rows,
        eligible=rows,
        missing_required=[],
        solver_status="infeasible",
    )

    assert evidence.knowledge_supply == {"极限": 1}
