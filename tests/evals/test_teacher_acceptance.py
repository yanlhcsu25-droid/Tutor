from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evals.case_loader import load_eval_suite
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.paper import seed_success_question_bank
from tests.evals.runner import create_eval_session, run_case, write_report


CASE_FILE = Path(__file__).parent / "cases" / "teacher_acceptance_v0.yaml"
REPORT_FILE = Path(__file__).parent / "reports" / "teacher_acceptance_v0.json"
EXPECTED_IDS = {
    "TD-001", "TD-002", "TD-003", "GEN-001", "GEN-002",
    "GEN-003", "MOD-001", "MOD-002", "PENDING-001", "ERR-001",
    "TD-004", "TD-005", "TD-006", "TD-007", "TD-008", "TD-009",
    "GEN-004", "GEN-005", "GEN-006", "MOD-003", "MOD-004",
}


def _diagnostic(result: dict) -> str:
    actual = result.get("actual") or {}
    trace = actual.get("trace") or {}
    tool_sequence = [
        item.get("tool_name") or item.get("name")
        for item in trace.get("tool_calls", [])
        if isinstance(item, dict)
    ]
    payload = {
        "case_id": result.get("case_id"),
        "expected": result.get("expected"),
        "actual": {
            "status": actual.get("status"),
            "message": actual.get("message"),
            "state": actual,
        },
        "tool_sequence": tool_sequence,
        "trace": trace,
        "runner_error": result.get("error"),
        "graders": result.get("graders"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def test_success_question_fixture_is_production_shaped() -> None:
    session = create_eval_session()
    try:
        seed_eval_curriculum(session)
        seed_success_question_bank(session)
        session.commit()
        from sqlalchemy import text

        assert session.execute(text("select count(*) from question")).scalar() == 14
        assert session.execute(text("select count(*) from question_profile")).scalar() == 14
        assert session.execute(text("select count(*) from question_knowledge_link")).scalar() == 28
        assert session.execute(
            text("select count(*) from question where solution_json like '%solution_steps%'")
        ).scalar() == 14
    finally:
        session.close()


def test_teacher_acceptance_suite_is_well_formed() -> None:
    suite = load_eval_suite(CASE_FILE)
    assert suite.name == "teacher_acceptance_v0"
    assert {case.id for case in suite.cases} == EXPECTED_IDS
    assert len(suite.cases) == 21
    assert all(case.turns for case in suite.cases)
    for case in suite.cases[:3]:
        acceptance = next(item for item in case.graders if item["type"] == "acceptance")
        assert "create_teaching_design" in acceptance["required_tools"]
        assert "confirm_teaching_design" in acceptance["forbidden_tools"]
        assert acceptance["statuses"] == ["waiting_confirmation"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Teacher Acceptance Evaluation requires RUN_LIVE_LLM=1",
)
def test_teacher_acceptance_v0() -> None:
    suite = load_eval_suite(CASE_FILE)
    results = [run_case(case) for case in suite.cases]
    write_report(results, REPORT_FILE)

    failures = [result for result in results if not result.get("passed")]
    if failures:
        pytest.fail(
            "Teacher Acceptance Evaluation failed:\n\n"
            + "\n\n".join(_diagnostic(result) for result in failures)
        )
