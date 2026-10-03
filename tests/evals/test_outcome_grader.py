from types import SimpleNamespace

from tests.evals.graders.outcome_grader import (
    grade_clarification_contract,
    grade_outcome_integrity,
)


def _turn(*, message, status="completed", calls=None, paper_changed=False):
    before_paper = {"paper": [], "paper_item": [], "active_pointer": [None, None]}
    after_paper = {**before_paper, "paper": [{"id": "p1"}]} if paper_changed else before_paper
    return {
        "turn": 1,
        "result": {"status": status, "message": message},
        "state": {"trace": {"tool_calls": calls or []}},
        "business_state_before": {"paper": before_paper, "teaching_design": {}},
        "business_state_after": {"paper": after_paper, "teaching_design": {}},
    }


def test_failed_tool_plus_success_claim_is_false_success():
    actual = {"evaluated_turns": [_turn(
        message="试卷已生成。",
        calls=[{"tool_name": "confirm_generation", "result": {"ok": False}}],
    )]}

    result = grade_outcome_integrity(None, actual)

    assert not result["passed"]
    assert result["false_success"] is True


def test_honest_failure_is_not_false_success():
    actual = {"evaluated_turns": [_turn(
        status="failed", message="生成失败，题库供给不足。",
        calls=[{"tool_name": "confirm_generation", "result": {"ok": False}}],
    )]}

    assert grade_outcome_integrity(None, actual)["passed"]


def test_paper_claim_requires_persisted_change():
    assert not grade_outcome_integrity(None, {
        "evaluated_turns": [_turn(message="试卷已生成。")],
    })["passed"]
    assert grade_outcome_integrity(None, {
        "evaluated_turns": [_turn(message="试卷已生成。", paper_changed=True)],
    })["passed"]


def test_clarification_requires_structured_question():
    case = SimpleNamespace(expected={"status": "needs_clarification"})
    result = grade_clarification_contract(case, {
        "status": "needs_clarification",
        "result": {"clarification_questions": []},
    })

    assert not result["passed"]
