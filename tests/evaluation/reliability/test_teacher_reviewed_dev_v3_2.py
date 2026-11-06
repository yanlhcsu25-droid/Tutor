from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from calculus_agent.agent.tools.analysis_tools import analyze_paper
from calculus_agent.models import CurriculumNode, Question
from evaluation.reliability.case_loader import EvalCase
from evaluation.reliability.curriculum_fixture import seed_eval_curriculum
from evaluation.reliability.evaluators.acceptance import grade_acceptance
from evaluation.reliability.evaluators.paper_analysis import grade_paper_analysis
from evaluation.reliability.evaluators.teaching_quality import (
    TeachingQualityInfrastructureError,
    grade_teaching_quality,
)
from evaluation.reliability.fixtures.paper import seed_current_paper, seed_question_bank
from evaluation.reliability.runner import run_case
from tests.conftest import create_isolated_test_session


BASE = Path("evaluation/reliability/datasets/staging/dev_v3_2_proposal")


def _cases(name: str) -> dict[str, dict]:
    payload = yaml.safe_load((BASE / name).read_text(encoding="utf-8"))
    return {case["id"]: case for case in payload["cases"]}


def test_reviewed_dataset_keeps_142_unique_active_cases() -> None:
    dev = _cases("dev_122_revised_candidate.yaml")
    devx = _cases("new_capability_20_candidate.yaml")

    assert len(dev) == 122
    assert len(devx) == 20
    assert len(set(dev) | set(devx)) == 142
    assert sum(
        case["review"]["status"] == "approved"
        for case in [*dev.values(), *devx.values()]
    ) == 129
    assert all(case["review"].get("technical_audit") for case in [*dev.values(), *devx.values()])
    assert "DEVX-015" not in devx
    assert "DEVX-021" in devx
    devx21_acceptance = next(
        grader for grader in devx["DEVX-021"]["graders"]
        if grader["type"] == "acceptance"
    )
    assert devx21_acceptance["response_equals_failed_tool_message"] is True
    assert dev["EVAL-149"]["expected"]["preview_paper_change"]["operations"] == [
        {"type": "replace_question", "position": 3}
    ]
    assert dev["EVAL-150"]["expected"]["status"] == "needs_clarification"
    assert {
        case_id
        for case_id in ("DEVX-003", "DEVX-019", "DEVX-021")
        if devx[case_id]["review"]["readiness"] == "NEEDS_CAPABILITY"
        and devx[case_id]["review"]["status"] == "pending"
    } == {"DEVX-003", "DEVX-019", "DEVX-021"}


def test_fixture_grader_runner_and_registry_have_no_case_id_branches() -> None:
    paths = [
        Path("evaluation/reliability/fixtures/paper.py"),
        Path("evaluation/reliability/evaluators/acceptance.py"),
        Path("evaluation/reliability/evaluators/paper_analysis.py"),
        Path("evaluation/reliability/evaluators/teaching_quality.py"),
        Path("evaluation/reliability/runner.py"),
        Path("src/calculus_agent/agent/paper_tool_registry.py"),
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "EVAL-" not in source
    assert "DEVX-" not in source


def test_chapter_three_question_bank_normalizes_solution_alias() -> None:
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        seed_question_bank(session, {
            "chapter_code": "三",
            "question_type_counts": {
                "选择题": 4,
                "填空题": 4,
                "计算题": 2,
                "解答题": 2,
                "证明题": 2,
            },
        })
        chapter = session.scalar(select(CurriculumNode).where(CurriculumNode.code == "三"))
        questions = list(session.scalars(
            select(Question).where(Question.curriculum_chapter_id == chapter.id)
        ))

        assert len(questions) == 14
        assert Counter(question.question_type for question in questions) == {
            "选择题": 4,
            "填空题": 4,
            "计算题": 4,
            "证明题": 2,
        }
    finally:
        session.close()


def test_question_bank_rejects_unknown_question_type() -> None:
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        with pytest.raises(ValueError, match="unknown question type"):
            seed_question_bank(session, {
                "chapter_code": "三",
                "question_type_counts": {"自定义题": 1},
            })
    finally:
        session.close()


def test_current_paper_position_knowledge_distribution_and_unknown_rejection() -> None:
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        context = seed_current_paper(session, {
            "question_count": 3,
            "total_score": 30,
            "knowledge_by_position": {
                1: ["函数与极限", "极限的运算法则"],
                2: ["函数与极限"],
                3: ["函数与极限"],
            },
        })
        result = analyze_paper(session, paper_id=context.paper_id)
        assert result.knowledge_distribution == {
            "函数与极限": 3,
            "极限的运算法则": 1,
        }
    finally:
        session.close()

    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        with pytest.raises(ValueError, match="unknown knowledge"):
            seed_current_paper(session, {
                "question_count": 1,
                "total_score": 10,
                "knowledge_by_position": {1: ["不存在的知识点"]},
            })
    finally:
        session.close()


def test_acceptance_can_require_exact_failed_tool_grounding() -> None:
    case = EvalCase(id="X", category="generation", title="x")
    actual = {
        "status": "failed",
        "message": "找不到历史试卷记录",
        "trace": {"tool_calls": [{
            "tool_name": "prepare_reinforcement_plan",
            "output_json": {
                "ok": False,
                "code": "no_current_paper",
                "message": "找不到历史试卷记录",
            },
        }]},
    }
    config = {"response_equals_failed_tool_message": True}

    assert grade_acceptance(case, actual, config)["passed"] is True
    actual["message"] += "，知识点是我猜的"
    assert grade_acceptance(case, actual, config)["passed"] is False


def test_paper_analysis_grader_checks_distribution_dominant_topic_and_no_write() -> None:
    distribution = {"函数与极限": 7, "极限的运算法则": 4}
    snapshot = {"paper": [1]}
    result = grade_paper_analysis(None, {
        "paper_analysis": {"knowledge_distribution": distribution},
        "message": "最集中的知识点是函数与极限。",
        "trace": {"tool_calls": [{
            "tool_name": "analyze_paper",
            "output_json": {"ok": True, "knowledge_distribution": distribution},
        }]},
        "evaluated_turns": [{"turn": 1, "paper_before": snapshot, "paper_after": snapshot}],
    }, {
        "knowledge_distribution": distribution,
        "association_count": 11,
        "dominant_knowledge": "函数与极限",
    })

    assert result["passed"] is True
    assert result["details"]["association_count"] == 11


class _JudgeAdapter:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error

    def complete(self, prompt):
        if self.error:
            raise self.error
        return self.payload

    def provenance(self):
        return {"calls": [{"response_id": "offline-fake"}]}


def _quality_case() -> tuple[EvalCase, dict]:
    case = EvalCase(
        id="EVAL-217",
        category="teaching",
        title="quality",
        turns=[{"user": "说明洛必达法则的条件"}],
    )
    config = {
        "judge_case_id": "TQ-CQ-217",
        "axes": {"mathematical_correctness": 0.6, "condition_rigor": 0.4},
        "reference_notes": ["检查数学正确性与条件严谨性"],
    }
    return case, config


def test_teaching_quality_grader_validates_schema_scores_and_provenance() -> None:
    case, config = _quality_case()
    payload = {
        "case_id": "TQ-CQ-217",
        "scores": {"mathematical_correctness": 4, "condition_rigor": 4},
        "critical_quality_failure": False,
        "failure_tags": [],
        "rationale": {
            "mathematical_correctness": "条件与结论正确。",
            "condition_rigor": "完整说明了适用前提。",
        },
        "overall_comment": "通过",
    }

    result = grade_teaching_quality(
        case, {"message": "离线测试回答"}, config, adapter=_JudgeAdapter(payload)
    )

    assert result["passed"] is True
    assert result["details"]["score"]["weighted_score"] == 4.0
    assert result["details"]["judge_output"]["rationale"]
    assert result["details"]["provider"]["calls"]


def test_teaching_quality_provider_or_schema_error_is_infrastructure_error() -> None:
    case, config = _quality_case()
    with pytest.raises(TeachingQualityInfrastructureError):
        grade_teaching_quality(
            case,
            {"message": "回答"},
            config,
            adapter=_JudgeAdapter(error=TimeoutError("offline timeout")),
        )
    with pytest.raises(TeachingQualityInfrastructureError):
        grade_teaching_quality(
            case,
            {"message": "回答"},
            config,
            adapter=_JudgeAdapter({"case_id": "invalid"}),
        )


def test_runner_requires_explicit_live_judge_and_classifies_gate_as_infrastructure(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RUN_LIVE_JUDGE", raising=False)
    case, config = _quality_case()
    case.expected = {"status": "completed"}
    case.graders = [{"type": "state"}, {"type": "teaching_quality", **config}]
    case.backend = {
        "behavior": "scripted",
        "responses": [{"message": {"role": "assistant", "content": "离线回答"}}],
    }

    result = run_case(case)

    assert result["execution_status"] == "infrastructure_failed"
    assert result["failure_type"] == "provider_failure"
    assert "RUN_LIVE_JUDGE=1" in result["error"]["message"]
