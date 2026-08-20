"""Phase 1 state-infrastructure tests.

These test ONLY the new persistence layer (Workspace pointer + Agent runtime
state).  They never touch the Agent, generation, tools, or the LLM.
"""

import pytest

from calculus_agent.agent.state import (
    InvalidStateTransitionError,
    RuntimeStateService,
    WorkspaceService,
)


# ── ConversationWorkspace ──


def test_workspace_get_or_create_when_missing(session):
    service = WorkspaceService(session)

    assert service.get("conv-missing") is None

    created = service.get_or_create("conv-missing")
    assert created.conversation_id == "conv-missing"
    assert created.active_type is None
    assert created.current_paper_id is None
    assert created.current_version_id is None

    # get_or_create is idempotent on the same conversation.
    again = service.get_or_create("conv-missing")
    assert again.conversation_id == "conv-missing"


def test_workspace_update_current_paper_persists(session):
    service = WorkspaceService(session)
    service.get_or_create("conv-1")

    service.update(
        "conv-1",
        {"current_paper_id": "paper-123", "current_version_id": "version-9"},
    )

    reread = service.get("conv-1")
    assert reread is not None
    assert reread.current_paper_id == "paper-123"
    assert reread.current_version_id == "version-9"
    assert reread.active_type is None


def test_workspace_update_rejects_unknown_fields(session):
    service = WorkspaceService(session)
    service.get_or_create("conv-1")

    with pytest.raises(ValueError):
        service.update("conv-1", {"current_paper_id": "p", "not_a_field": 1})


# ── AgentRuntimeState ──


def test_runtime_state_initial_phase_is_idle(session):
    service = RuntimeStateService(session)

    state = service.get_or_create("conv-1")

    assert state.phase == "idle"
    assert state.revision == 0
    assert state.task_type is None
    assert state.waiting_for is None


def test_runtime_state_legal_transition(session):
    service = RuntimeStateService(session)

    state = service.transition("conv-1", "planning", task_type="generation")

    assert state.phase == "planning"
    assert state.task_type == "generation"


def test_runtime_state_illegal_transition_fails(session):
    service = RuntimeStateService(session)
    service.get_or_create("conv-1")  # idle

    with pytest.raises(InvalidStateTransitionError):
        service.transition("conv-1", "completed")


def test_runtime_state_revision_increments(session):
    service = RuntimeStateService(session)

    state = service.transition("conv-1", "planning")  # idle -> planning
    assert state.revision == 1

    state = service.transition(
        "conv-1",
        "waiting",
        waiting_for="teacher_confirmation",
    )  # planning -> waiting
    assert state.revision == 2
    assert state.waiting_for == "teacher_confirmation"
