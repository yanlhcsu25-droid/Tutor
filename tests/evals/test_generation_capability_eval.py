from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.evals.case_loader import EvalCase
from tests.evals.runner import run_case


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Generation capability eval requires RUN_LIVE_LLM=1",
)


DEFAULT_SCOPE = ["第一章 函数与极限"]


def _default_pending_fixture() -> dict[str, Any]:
    """A deterministic chapter-test pending state before teacher overrides."""
    return {
        "paper_type": "chapter_test",
        "scope_names": DEFAULT_SCOPE,
        "question_count": 10,
        "total_score": 100,
        "total_score_source": "default_template",
        "question_type_requirements": [
            {
                "question_type": "选择题",
                "count": 4,
            },
            {
                "question_type": "填空题",
                "count": 2,
            },
            {
                "question_type": "计算题",
                "count": 4,
            },
        ],
    }


def _tool_names(result: dict[str, Any]) -> set[str]:
    actual = result.get("actual") or {}
    trace = actual.get("trace") or {}
    tool_calls = trace.get("tool_calls") or []

    names: set[str] = set()

    for item in tool_calls:
        if not isinstance(item, dict):
            continue

        name = (
            item.get("name")
            or item.get("tool")
            or item.get("tool_name")
        )
        if isinstance(name, str) and name:
            names.add(name)

    return names


def _compact_result(result: dict[str, Any]) -> str:
    """Keep pytest failures readable instead of dumping the entire trace."""
    actual = result.get("actual") or {}
    pending = actual.get("pending_generation") or {}
    graders = result.get("graders") or []

    trace = actual.get("trace") or {}

    compact = {
        "case_id": result.get("case_id"),
        "passed": result.get("passed"),
        "runner_error": result.get("error"),
        "runtime_error": actual.get("error"),
        "trace_error": {
            "code": trace.get("error_code"),
            "type": trace.get("error_type"),
            "stage": trace.get("error_stage"),
            "message": trace.get("error_message"),
        },
        "status": actual.get("status"),
        "message": (actual.get("message") or "")[:500],
        "pending_generation": {
            "paper_type": pending.get("paper_type"),
            "scope_names": pending.get("scope_names"),
            "question_count": pending.get("question_count"),
            "total_score": pending.get("total_score"),
            "total_score_source": pending.get("total_score_source"),
            "locked_score_question_types": pending.get(
                "locked_score_question_types"
            ),
            "question_type_requirements": pending.get(
                "question_type_requirements"
            ),
        },
        "tools": sorted(_tool_names(result)),
        "grader_errors": [
            {
                "grader": grader.get("grader"),
                "errors": grader.get("errors"),
            }
            for grader in graders
            if grader.get("passed") is False
        ],
    }

    return json.dumps(compact, ensure_ascii=False, indent=2)


def _assert_case_passed(result: dict[str, Any]) -> None:
    assert result.get("error") is None, _compact_result(result)
    assert result.get("passed") is True, _compact_result(result)


def test_generation_default_uses_deterministic_chapter_template() -> None:
    """
    Capability:
    Teacher does not specify score/count/type distribution.

    Expected:
    - chapter_test deterministic defaults are used;
    - default total score is allowed;
    - provenance must not pretend the teacher explicitly requested 100;
    - no generation confirmation occurs without teacher confirmation.
    """
    case = EvalCase(
        id="GEN-CAP-DEFAULT-01",
        category="generation_capability",
        title="Default chapter test uses deterministic template",
        turns=[
            {
                "user": "帮我出一套高数第一章测试卷。",
            }
        ],
        expected={
            "status": "waiting_confirmation",
            "pending_generation": {
                "paper_type": "chapter_test",
                "question_count": 10,
                "total_score": 100,
                "total_score_source": "default_template",
                "question_type_requirements": [
                    {
                        "question_type": "选择题",
                        "count": 4,
                    },
                    {
                        "question_type": "填空题",
                        "count": 2,
                    },
                    {
                        "question_type": "计算题",
                        "count": 4,
                    },
                ],
            },
        },
        graders=[
            {"type": "state"},
            {"type": "score"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)
    assert "confirm_generation" not in _tool_names(result), (
        _compact_result(result)
    )


def test_generation_teacher_score_overrides_default() -> None:
    """
    Capability:
    Existing pending plan has a system default of 100.
    Teacher explicitly changes it to 60.

    Expected:
    teacher_explicit > default_template.
    """
    case = EvalCase(
        id="GEN-CAP-OVERRIDE-01",
        category="generation_capability",
        title="Teacher explicit total score overrides default",
        setup={
            "pending_generation": _default_pending_fixture(),
        },
        turns=[
            {
                "user": "总分改成60分。",
            }
        ],
        expected={
            "status": "waiting_confirmation",
            "pending_generation": {
                "question_count": 10,
                "total_score": 60,
                "total_score_source": "teacher_explicit",
                "question_type_requirements": [
                    {
                        "question_type": "选择题",
                        "count": 4,
                    },
                    {
                        "question_type": "填空题",
                        "count": 2,
                    },
                    {
                        "question_type": "计算题",
                        "count": 4,
                    },
                ],
            },
        },
        graders=[
            {"type": "state"},
            {"type": "score"},
            {"type": "constraint_preservation"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)
    assert "confirm_generation" not in _tool_names(result), (
        _compact_result(result)
    )


def test_generation_multiturn_merge_preserves_teacher_constraints() -> None:
    """
    Capability:
    Multi-turn editing of the same PendingGeneration.

    Turn 1: default 100 -> teacher 60
    Turn 2: calculation count 4 -> 5
    Turn 3: teacher score 60 -> 80

    Expected:
    later patches only replace fields explicitly changed by the teacher.
    """
    case = EvalCase(
        id="GEN-CAP-MERGE-01",
        category="generation_capability",
        title="Multi-turn generation merge preserves explicit constraints",
        setup={
            "pending_generation": _default_pending_fixture(),
        },
        turns=[
            {
                "user": "总分改成60分。",
            },
            {
                "user": "计算题改成5道。",
            },
            {
                "user": "总分还是改成80分吧。",
            },
        ],
        expected={
            "status": "waiting_confirmation",
            "pending_generation": {
                "question_count": 11,
                "total_score": 80,
                "total_score_source": "teacher_explicit",
                "question_type_requirements": [
                    {
                        "question_type": "选择题",
                        "count": 4,
                    },
                    {
                        "question_type": "填空题",
                        "count": 2,
                    },
                    {
                        "question_type": "计算题",
                        "count": 5,
                    },
                ],
            },
        },
        graders=[
            {"type": "state"},
            {"type": "score"},
            {"type": "constraint_preservation"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)
    assert "confirm_generation" not in _tool_names(result), (
        _compact_result(result)
    )


def test_generation_locked_score_conflict_requires_clarification() -> None:
    """
    Capability:
    Teacher total score is explicitly locked at 60, while every section score
    is also locked and the section totals add up to only 55.

    Expected:
    - do not silently change teacher total_score to 55;
    - do not mutate locked section scores;
    - return clarification before producing a paper.
    """
    case = EvalCase(
        id="GEN-CAP-CONFLICT-01",
        category="generation_capability",
        title="Locked score conflict requires clarification",
        setup={
            "pending_generation": {
                "paper_type": "chapter_test",
                "scope_names": DEFAULT_SCOPE,
                "question_count": 10,
                "total_score": 60,
                "total_score_source": "teacher_explicit",
                "locked_score_question_types": [
                    "选择题",
                    "填空题",
                    "计算题",
                ],
                "question_type_requirements": [
                    {
                        "question_type": "选择题",
                        "count": 4,
                        "score_each": 5,
                        "total_score": 20,
                    },
                    {
                        "question_type": "填空题",
                        "count": 2,
                        "score_each": 5,
                        "total_score": 10,
                    },
                    {
                        "question_type": "计算题",
                        "count": 4,
                        "score_each": 6.25,
                        "total_score": 25,
                    },
                ],
            },
        },
        turns=[
            {
                "user": "确认，就按这个。",
            }
        ],
        expected={
            "status": "needs_clarification",
            "pending_generation": {
                "total_score": 60,
                "total_score_source": "teacher_explicit",
                "locked_score_question_types": [
                    "选择题",
                    "填空题",
                    "计算题",
                ],
                "question_type_requirements": [
                    {
                        "question_type": "选择题",
                        "count": 4,
                        "score_each": 5,
                        "total_score": 20,
                    },
                    {
                        "question_type": "填空题",
                        "count": 2,
                        "score_each": 5,
                        "total_score": 10,
                    },
                    {
                        "question_type": "计算题",
                        "count": 4,
                        "score_each": 6.25,
                        "total_score": 25,
                    },
                ],
            },
        },
        graders=[
            {"type": "state"},
            {"type": "score"},
            {"type": "constraint_preservation"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)

    actual = result.get("actual") or {}
    paper = actual.get("paper") or {}

    assert paper.get("paper_id") is None, _compact_result(result)
