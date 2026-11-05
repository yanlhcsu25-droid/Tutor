from calculus_agent.agent.schemas import GenerationConstraints, PaperGenerationRequest
from calculus_agent.agent.tools import paper_tools
from calculus_agent.generation_diagnosis import (
    GenerationDiagnosis,
    RecoveryActionType,
    SelectionEvidence,
    decide_recovery,
)
from calculus_agent.models import Paper
from calculus_agent.schemas import PaperBlueprint, PaperPreviewRead


def _request() -> PaperGenerationRequest:
    return PaperGenerationRequest(
        blueprint=PaperBlueprint(
            title="failure-loop-eval",
            total_questions=5,
            total_score=50,
            question_type_counts={"证明题": 5},
            seed=42,
        ),
        constraints=GenerationConstraints(),
    )


def _diagnosis(code: str, failure_class: str = "design") -> GenerationDiagnosis:
    return GenerationDiagnosis(
        failure_class=failure_class,
        code=code,
        recoverability="unknown",
    )


def test_insufficient_question_supply_does_not_persist_and_asks_user(
    session, monkeypatch
):
    preview = PaperPreviewRead(
        title="failure-loop-eval",
        total_score=0,
        items=[],
        constraints=[],
        warnings=[],
        feasible=False,
    )
    evidence = SelectionEvidence(
        candidate_count=1,
        eligible_count=1,
        type_supply={"证明题": 1},
        solver_status="infeasible",
    )
    monkeypatch.setattr(
        paper_tools,
        "compose_paper_with_evidence",
        lambda _session, _request: (preview, evidence),
    )

    result = paper_tools._execute_generation_request(session, _request(), warnings=[])

    assert result.ok is False
    # The current deterministic taxonomy calls this type shortage; it is the
    # concrete equivalent of the requested insufficient-candidates case.
    assert result.diagnosis is not None
    assert result.diagnosis.code == "type_supply_shortage"
    assert result.recovery_action.action_type == RecoveryActionType.ASK_USER
    assert session.query(Paper).count() == 0


def test_missing_scope_asks_user():
    action = decide_recovery(_diagnosis("scope_not_found"))
    assert action.action_type == RecoveryActionType.ASK_USER


def test_pending_generation_conflict_asks_user():
    action = decide_recovery(_diagnosis("pending_generation_exists"))
    assert action.action_type == RecoveryActionType.ASK_USER


def test_non_executable_teaching_design_requires_revision():
    action = decide_recovery(_diagnosis("teaching_design_not_executable"))
    assert action.action_type == RecoveryActionType.REVISE_DESIGN


def test_technical_failure_recommends_retry_without_auto_execution():
    action = decide_recovery(_diagnosis("technical_failure", "technical"))
    assert action.action_type == RecoveryActionType.AUTO_RETRY
    assert action.auto_executable is False
