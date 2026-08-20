from calculus_agent.generation_diagnosis import (
    GenerationDiagnosis,
    RecoveryActionType,
    decide_recovery,
)


def _diagnosis(code: str) -> GenerationDiagnosis:
    return GenerationDiagnosis(
        failure_class="design" if code != "technical_failure" else "technical",
        code=code,
        recoverability="unknown",
    )


def test_insufficient_candidates_asks_user():
    action = decide_recovery(_diagnosis("insufficient_candidates"))
    assert action.action_type == RecoveryActionType.ASK_USER


def test_technical_failure_can_auto_retry():
    action = decide_recovery(_diagnosis("technical_failure"))
    assert action.action_type == RecoveryActionType.AUTO_RETRY
    assert action.auto_executable is False


def test_non_executable_design_requires_revision():
    action = decide_recovery(_diagnosis("teaching_design_not_executable"))
    assert action.action_type == RecoveryActionType.REVISE_DESIGN


def test_pending_generation_asks_user():
    action = decide_recovery(_diagnosis("pending_generation_exists"))
    assert action.action_type == RecoveryActionType.ASK_USER


def test_unknown_failure_aborts():
    action = decide_recovery(_diagnosis("unknown_generation_failure"))
    assert action.action_type == RecoveryActionType.ABORT
