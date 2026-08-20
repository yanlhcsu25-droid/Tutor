"""Phase 2 regression tests for AgentRuntimeState generation lifecycle.

Only verifies lifecycle stability.
No LLM calls.
No real paper generation.
"""

import calculus_agent.agent.services.generation as generation_module

from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)

from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    GenerationPlanPatch,
)

from calculus_agent.agent.services.generation import GenerationService

from calculus_agent.agent.state.service import RuntimeStateService

from tests.teacher_agent.test_agent_state_generation_lifecycle import (
    _fake_generation_request,
)


def test_generation_state_does_not_use_working_memory_as_source_of_truth(
    session, monkeypatch
):
    monkeypatch.setattr(
        generation_module,
        "build_structured_generation_request",
        lambda session, request: (_fake_generation_request(), [], [], []),
    )

    conversation_id = "conv-memory-conflict"
    store = DatabasePendingReplacementStore(session)
    # Store a valid pending plan independently of the conversational summary.
    store.set_generation(
        conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["pending-scope"],
            )
        ),
    )
    memory = store.get_memory(conversation_id)
    memory.active_task = {"type": "generation", "status": "completed"}
    memory.generation_summary = {
        "paper_type": "homework",
        "scope_names": ["memory-scope"],
    }
    store.set_memory(conversation_id, memory)

    service = GenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
        runtime_state_service=RuntimeStateService(session),
    )
    preview = service.preview(GenerationPlanPatch(paper_type="chapter_exercise"))

    assert preview.ok is True
    saved_request = store.get_generation(conversation_id).request
    assert saved_request.paper_type == "chapter_exercise"
    assert saved_request.scope_names == ["pending-scope"]
    assert RuntimeStateService(session).get(conversation_id).phase == "waiting"


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
