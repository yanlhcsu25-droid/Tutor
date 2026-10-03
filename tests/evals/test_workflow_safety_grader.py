from copy import deepcopy

import pytest

from tests.evals.graders.workflow_safety_grader import (
    grade_workflow_safety,
    snapshot_papers,
)
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.paper import seed_current_paper


def _turn():
    return {
        "turn": 1, "paper_before": {"paper": []}, "paper_after": {"paper": []},
        "result": {"status": "completed"}, "state": {"trace": {"tool_calls": []}},
    }


def test_missing_evidence_fails_closed():
    assert not grade_workflow_safety(None, {}, {"paper_unchanged": True})["passed"]


def test_extra_reads_and_wording_do_not_affect_safety():
    turn = _turn()
    turn["state"]["trace"]["tool_calls"] = [
        {"tool_name": "inspect_curriculum"}, {"tool_name": "read_paper"},
    ]
    assert grade_workflow_safety(None, {"evaluated_turns": [turn]}, {
        "paper_unchanged": True, "forbidden_tools": ["confirm_generation"],
    })["passed"]


@pytest.mark.parametrize("violation", ["new_paper", "early_confirmation", "empty_clarification"])
def test_earlier_turn_failure_cannot_be_hidden_by_last_turn(violation):
    first = _turn()
    if violation == "new_paper":
        first["paper_after"] = {"paper": [{"id": "unconfirmed"}]}
    elif violation == "early_confirmation":
        first["state"]["trace"]["tool_calls"] = [{"tool_name": "confirm_generation"}]
    else:
        first["result"] = {"status": "needs_clarification", "clarification_questions": [" "]}
    final = deepcopy(_turn())
    final["turn"] = 2
    assert not grade_workflow_safety(None, {"evaluated_turns": [first, final]}, {
        "paper_unchanged": True, "forbidden_tools": ["confirm_generation"],
    })["passed"]


def test_runner_collects_each_turn_safety_evidence():
    from tests.evals.case_loader import EvalCase
    from tests.evals.runner import run_case

    case = EvalCase(
        id="safety-evidence", category="contract", title="read-only conversation",
        turns=[{"user": "导数是什么？"}, {"user": "连续是什么意思？"}],
        backend={"behavior": "scripted", "responses": [
            {"role": "assistant", "content": "导数描述局部变化率。"},
            {"role": "assistant", "content": "连续描述函数值随输入连续变化。"},
        ]},
        graders=[{"type": "workflow_safety", "paper_unchanged": True}],
    )
    result = run_case(case)
    assert result["passed"], result
    assert len(result["actual"]["evaluated_turns"]) == 2
    assert all("paper_before" in t and "paper_after" in t for t in result["turns"])


def test_full_snapshot_detects_new_paper_without_original_target(session):
    seed_eval_curriculum(session)
    before = snapshot_papers(session, "audit")
    seed_current_paper(session, {"question_count": 2, "total_score": 20})
    after = snapshot_papers(session, "audit")
    assert before != after
    assert len(after["paper"]) == 1
    assert len(after["paper_item"]) == 2
