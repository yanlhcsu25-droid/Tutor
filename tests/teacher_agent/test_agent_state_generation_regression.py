"""Phase 2 regression tests for AgentRuntimeState generation lifecycle.

Only verifies lifecycle stability.
No LLM calls.
No real paper generation.
"""

import calculus_agent.agent.services.generation as generation_module

from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
)

from calculus_agent.agent.schemas import (
    GenerationPlanPatch,
)

from calculus_agent.agent.services.generation import GenerationService

from calculus_agent.agent.state.service import RuntimeStateService

from tests.teacher_agent.test_agent_state_generation_lifecycle import (
    _fake_generation_request,
)


def test_prepare_generation_twice_keeps_waiting_state(session, monkeypatch):
    """Repeated prepare should be idempotent.

    waiting -> waiting should not fail or create meaningless state changes.
    """

    monkeypatch.setattr(
        generation_module,
        "build_structured_generation_request",
        lambda session, request: (
            _fake_generation_request(),
            [],
            [],
            [],
        ),
    )

    conversation_id = "conv-double-prepare"

    runtime_state_service = RuntimeStateService(session)

    service = GenerationService(
        session=session,
        store=DatabasePendingReplacementStore(session),
        conversation_id=conversation_id,
        runtime_state_service=runtime_state_service,
    )

    first = service.preview(
        GenerationPlanPatch(
            paper_type="chapter_test",
        )
    )

    assert first.ok is True

    state_after_first = runtime_state_service.get(
        conversation_id
    )

    assert state_after_first is not None
    assert state_after_first.phase == "waiting"
    assert state_after_first.task_type == "generation"
    assert (
        state_after_first.waiting_for
        == "teacher_confirmation"
    )

    first_revision = state_after_first.revision

    second = service.preview(
        GenerationPlanPatch(
            paper_type="chapter_test",
        )
    )

    assert second.ok is True

    state_after_second = runtime_state_service.get(
        conversation_id
    )

    assert state_after_second is not None
    assert state_after_second.phase == "waiting"
    assert state_after_second.task_type == "generation"
    assert (
        state_after_second.waiting_for
        == "teacher_confirmation"
    )

    # Same phase transition should be a no-op.
    assert state_after_second.revision == first_revision