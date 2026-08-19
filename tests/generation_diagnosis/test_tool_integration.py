from __future__ import annotations

from datetime import UTC, datetime

from calculus_agent.agent.schemas import (
    GenerationConstraints,
    PaperGenerationRequest,
)
from calculus_agent.agent.tools import paper_tools
from calculus_agent.generation_diagnosis import SelectionEvidence
from calculus_agent.schemas import (
    ConstraintViolationRead,
    PaperBlueprint,
    PaperPreviewRead,
    ValidationReportRead,
)


def _request() -> PaperGenerationRequest:
    return PaperGenerationRequest(
        blueprint=PaperBlueprint(
            title="T4-2B integration",
            total_questions=2,
            total_score=20,
            question_type_counts={
                "计算题": 1,
                "证明题": 1,
            },
            seed=42,
        ),
        constraints=GenerationConstraints(),
    )


def test_selection_failure_diagnosis_is_exposed_on_tool_result(monkeypatch):
    preview = PaperPreviewRead(
        title="T4-2B integration",
        total_score=10,
        items=[],
        constraints=[],
        warnings=["任意展示文案"],
        feasible=False,
    )
    evidence = SelectionEvidence(
        candidate_count=4,
        eligible_count=4,
        type_supply={
            "计算题": 4,
            "证明题": 0,
        },
        knowledge_supply={},
        image_supply=0,
        missing_required_question_ids=[],
        solver_status="infeasible",
    )

    monkeypatch.setattr(
        paper_tools,
        "compose_paper_with_evidence",
        lambda _session, _request: (preview, evidence),
    )

    result = paper_tools._execute_generation_request(
        None,
        _request(),
        warnings=[],
    )

    assert result.ok is False
    assert result.diagnosis is not None
    assert result.diagnosis.code == "type_supply_shortage"
    assert result.diagnosis.failure_class == "design"

    dumped = result.model_dump(mode="json")
    assert dumped["diagnosis"]["code"] == "type_supply_shortage"
    assert dumped["diagnosis"]["facts"][0]["subject"] == "证明题"
    assert dumped["diagnosis"]["facts"][0]["required"] == 1
    assert dumped["diagnosis"]["facts"][0]["available"] == 0


def test_validation_failure_diagnosis_is_exposed_without_changing_t4_semantics(
    monkeypatch,
):
    request = _request()
    preview = PaperPreviewRead(
        title="T4-2B integration",
        total_score=20,
        items=[],
        constraints=[],
        warnings=[],
        feasible=True,
    )
    evidence = SelectionEvidence(
        candidate_count=2,
        eligible_count=2,
        type_supply={
            "计算题": 1,
            "证明题": 1,
        },
        knowledge_supply={},
        image_supply=0,
        missing_required_question_ids=[],
        solver_status="optimal",
    )

    monkeypatch.setattr(
        paper_tools,
        "compose_paper_with_evidence",
        lambda _session, _request: (preview, evidence),
    )

    class Persisted:
        ok = True
        paper_id = "paper-1"
        version_id = "paper-version-1"
        warnings = []
        blocking_errors = []

    monkeypatch.setattr(
        paper_tools,
        "create_paper_draft",
        lambda *args, **kwargs: Persisted(),
    )

    validation_report = ValidationReportRead(
        id="validation-1",
        paper_id="paper-1",
        passed=False,
        violations=[
            ConstraintViolationRead(
                code="test_violation",
                field="paper",
                required="valid",
                actual="invalid",
                repairable=True,
                message="test violation",
            )
        ],
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        paper_tools,
        "validate_paper",
        lambda *_args, **_kwargs: validation_report,
    )

    result = paper_tools._execute_generation_request(
        None,
        request,
        warnings=[],
    )

    # T4 boundary: validation failed must never be reported as goal success.
    assert result.ok is False
    assert result.validation_status == "failed"
    assert result.validation_report == validation_report
    assert result.diagnosis is not None
    assert result.diagnosis.code == "paper_validation_failed"
    assert result.diagnosis.failure_class == "artifact"

    dumped = result.model_dump(mode="json")
    assert dumped["diagnosis"]["code"] == "paper_validation_failed"


def test_success_result_has_no_failure_diagnosis(monkeypatch):
    request = _request()
    preview = PaperPreviewRead(
        title="T4-2B integration",
        total_score=20,
        items=[],
        constraints=[],
        warnings=[],
        feasible=True,
    )
    evidence = SelectionEvidence(
        candidate_count=2,
        eligible_count=2,
        type_supply={
            "计算题": 1,
            "证明题": 1,
        },
        knowledge_supply={},
        image_supply=0,
        missing_required_question_ids=[],
        solver_status="optimal",
    )

    monkeypatch.setattr(
        paper_tools,
        "compose_paper_with_evidence",
        lambda _session, _request: (preview, evidence),
    )

    class Persisted:
        ok = True
        paper_id = "paper-1"
        version_id = "paper-version-1"
        warnings = []
        blocking_errors = []

    monkeypatch.setattr(
        paper_tools,
        "create_paper_draft",
        lambda *args, **kwargs: Persisted(),
    )

    validation_report = ValidationReportRead(
        id="validation-1",
        paper_id="paper-1",
        passed=True,
        violations=[],
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        paper_tools,
        "validate_paper",
        lambda *_args, **_kwargs: validation_report,
    )

    result = paper_tools._execute_generation_request(
        None,
        request,
        warnings=[],
    )

    assert result.ok is True
    assert result.validation_status == "passed"
    assert result.diagnosis is None
