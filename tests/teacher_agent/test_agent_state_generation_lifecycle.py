"""Phase 2 tests: AgentRuntimeState participates in the generation lifecycle.

These tests verify the runtime-state wiring only.  They never call the LLM and
never build a real paper: ``build_structured_generation_request`` and
``generate_paper_from_input`` are monkeypatched with deterministic fakes.
"""

import pytest
import calculus_agent.agent.services.generation as generation_module
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    GenerationPlanPatch,
    PaperGenerationRequest,
)
from calculus_agent.agent.services.generation import GenerationLifecycleError, GenerationService
from calculus_agent.agent.state.service import RuntimeStateService, WorkspaceService
from calculus_agent.agent.tools.paper_tools import (
    GeneratePaperToolResult,
    PaperSummary,
)
from calculus_agent.models import Paper, PaperBlueprintRecord
from calculus_agent.schemas import PaperBlueprint, SectionRequirement


# ── State machine: fast-lane edges added in Phase 2 ──


def test_runtime_state_fast_lane_edges_are_allowed(session):
    service = RuntimeStateService(session)

    service.transition("conv-1", "waiting")  # idle -> waiting
    assert service.get("conv-1").phase == "waiting"

    service.transition("conv-1", "completed")  # waiting -> completed
    assert service.get("conv-1").phase == "completed"

    service.transition("conv-2", "waiting")
    service.transition("conv-2", "idle")  # waiting -> idle (discard)
    assert service.get("conv-2").phase == "idle"


# ── prepare_generation_plan success -> waiting ──


def _fake_generation_request() -> PaperGenerationRequest:
    blueprint = PaperBlueprint(
        title="测试卷",
        total_questions=1,
        total_score=10,
        sections=[
            SectionRequirement(
                question_type="计算题",
                count=1,
                score_per_question=10,
                total_score=10,
            )
        ],
    )
    return PaperGenerationRequest(blueprint=blueprint)


def test_preview_success_transitions_to_waiting(session, monkeypatch):
    monkeypatch.setattr(
        generation_module,
        "build_structured_generation_request",
        lambda session, request: (_fake_generation_request(), [], [], []),
    )

    store = DatabasePendingReplacementStore(session)
    service = GenerationService(
        session=session,
        store=store,
        conversation_id="conv-gen",
        runtime_state_service=RuntimeStateService(session),
    )

    preview = service.preview(GenerationPlanPatch(paper_type="chapter_test"))

    assert preview.ok is True
    state = RuntimeStateService(session).get("conv-gen")
    assert state is not None
    assert state.phase == "waiting"
    assert state.task_type == "generation"
    assert state.waiting_for == "teacher_confirmation"


# ── confirm_generation success / failure ──


def _make_blueprint(session) -> PaperBlueprintRecord:
    blueprint = PaperBlueprintRecord(
        title="测试卷",
        blueprint_json={"title": "测试卷", "total_questions": 1, "total_score": 10},
        status="used",
    )
    session.add(blueprint)
    session.flush()
    return blueprint


def _make_service(session, conversation_id: str) -> GenerationService:
    return GenerationService(
        session=session,
        store=DatabasePendingReplacementStore(session),
        conversation_id=conversation_id,
        runtime_state_service=RuntimeStateService(session),
        workspace_service=WorkspaceService(session),
    )


def _seed_pending(session, conversation_id: str) -> None:
    DatabasePendingReplacementStore(session).set_generation(
        conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["第一章"],
                total_score=10,
            ),
        ),
    )


def test_confirm_success_transitions_to_completed(session, monkeypatch):
    conversation_id = "conv-confirm"
    blueprint = _make_blueprint(session)

    def fake_generate(session, request):
        paper = Paper(
            blueprint_id=blueprint.id,
            version=1,
            status="passed",
            title="测试卷",
            total_score=10,
            validation_status="passed",
        )
        session.add(paper)
        session.flush()
        paper.root_paper_id = paper.id
        session.flush()
        return GeneratePaperToolResult(
            ok=True,
            paper_id=paper.id,
            version_id=paper.id,
            summary=PaperSummary(
                total_questions=1,
                total_score=10,
                question_type_counts={"计算题": 1},
            ),
            validation_status="passed",
        )

    monkeypatch.setattr(generation_module, "generate_paper_from_input", fake_generate)

    _seed_pending(session, conversation_id)
    # A real prepare step would already have moved us to ``waiting``.
    RuntimeStateService(session).transition(
        conversation_id,
        "waiting",
        task_type="generation",
        waiting_for="teacher_confirmation",
    )

    result = _make_service(session, conversation_id).confirm()

    assert result.ok is True
    state = RuntimeStateService(session).get(conversation_id)
    assert state.phase == "completed"
    assert state.task_type == "generation"


def test_pending_generation_is_not_stored_in_workspace(session, monkeypatch):
    monkeypatch.setattr(
        generation_module,
        "build_structured_generation_request",
        lambda session, request: (_fake_generation_request(), [], [], []),
    )

    conversation_id = "conv-workspace-boundary"
    store = DatabasePendingReplacementStore(session)
    service = GenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
        runtime_state_service=RuntimeStateService(session),
        workspace_service=WorkspaceService(session),
    )

    preview = service.preview(GenerationPlanPatch(paper_type="chapter_test"))

    assert preview.ok is True
    assert store.get_generation(conversation_id) is not None
    workspace = WorkspaceService(session).get(conversation_id)
    assert workspace is not None
    assert workspace.active_type == "paper"
    assert workspace.current_paper_id is None
    assert workspace.current_version_id is None
    assert not hasattr(workspace, "pending_generation_id")


def test_confirm_clears_pending_and_only_updates_workspace_pointers(session, monkeypatch):
    conversation_id = "conv-confirm-boundary"
    blueprint = _make_blueprint(session)

    def fake_generate(session, request):
        paper = Paper(
            blueprint_id=blueprint.id,
            version=1,
            status="passed",
            title="测试卷",
            total_score=10,
            validation_status="passed",
        )
        session.add(paper)
        session.flush()
        paper.root_paper_id = paper.id
        session.flush()
        return GeneratePaperToolResult(
            ok=True,
            paper_id=paper.id,
            version_id=paper.id,
            summary=PaperSummary(
                total_questions=1,
                total_score=10,
                question_type_counts={"计算题": 1},
            ),
            validation_status="passed",
        )

    monkeypatch.setattr(generation_module, "generate_paper_from_input", fake_generate)

    workspace_service = WorkspaceService(session)
    workspace_service.update(
        conversation_id,
        {"active_type": "paper", "current_paper_id": "old-paper"},
    )
    _seed_pending(session, conversation_id)
    RuntimeStateService(session).transition(
        conversation_id,
        "waiting",
        task_type="generation",
        waiting_for="teacher_confirmation",
    )

    result = _make_service(session, conversation_id).confirm()

    assert result.ok is True
    assert DatabasePendingReplacementStore(session).get_generation(conversation_id) is None
    workspace = workspace_service.get(conversation_id)
    assert workspace is not None
    assert workspace.active_type == "paper"
    assert workspace.current_paper_id == str(result.paper_id)
    assert workspace.current_version_id == str(result.version_id)
    assert not hasattr(workspace, "pending_generation_id")


def test_confirm_failure_transitions_to_failed(session, monkeypatch):
    conversation_id = "conv-fail"

    def fake_generate_fail(session, request):
        return GeneratePaperToolResult(
            ok=False,
            blocking_errors=["generation_failed"],
        )

    monkeypatch.setattr(generation_module, "generate_paper_from_input", fake_generate_fail)

    _seed_pending(session, conversation_id)
    RuntimeStateService(session).transition(
        conversation_id,
        "waiting",
        task_type="generation",
        waiting_for="teacher_confirmation",
    )

    result = _make_service(session, conversation_id).confirm()

    assert result.ok is False
    state = RuntimeStateService(session).get(conversation_id)
    assert state.phase == "failed"
    assert state.task_type == "generation"


def test_confirm_out_of_band_phase_does_not_break_generation(session, monkeypatch):
    """A legacy pending plan is explicitly reconciled from idle to waiting."""
    conversation_id = "conv-legacy"
    blueprint = _make_blueprint(session)

    def fake_generate(session, request):
        paper = Paper(
            blueprint_id=blueprint.id,
            version=1,
            status="passed",
            title="测试卷",
            total_score=10,
            validation_status="passed",
        )
        session.add(paper)
        session.flush()
        paper.root_paper_id = paper.id
        session.flush()
        return GeneratePaperToolResult(
            ok=True,
            paper_id=paper.id,
            version_id=paper.id,
            summary=PaperSummary(total_questions=1, total_score=10),
            validation_status="passed",
        )

    monkeypatch.setattr(generation_module, "generate_paper_from_input", fake_generate)

    _seed_pending(session, conversation_id)
    # NOTE: RuntimeState is absent for this legacy record and is reconciled.

    result = _make_service(session, conversation_id).confirm()

    assert result.ok is True
    assert RuntimeStateService(session).get(conversation_id).phase == "completed"


def test_preview_fails_closed_when_runtime_is_executing(session, monkeypatch):
    monkeypatch.setattr(
        generation_module,
        "build_structured_generation_request",
        lambda session, request: (_fake_generation_request(), [], [], []),
    )
    conversation_id = "conv-illegal-preview"
    state = RuntimeStateService(session)
    state.transition(conversation_id, "waiting")
    state.transition(conversation_id, "executing")
    store = DatabasePendingReplacementStore(session)

    with pytest.raises(GenerationLifecycleError, match="generation_lifecycle_transition_failed"):
        GenerationService(
            session=session,
            store=store,
            conversation_id=conversation_id,
            runtime_state_service=state,
        ).preview(GenerationPlanPatch(paper_type="chapter_test"))

    # Runtime rejection happens before the new pending generation is persisted.
    assert store.get_generation(conversation_id) is None


def test_confirm_rejects_executing_pending_without_generating(session, monkeypatch):
    conversation_id = "conv-executing-pending"
    store = DatabasePendingReplacementStore(session)
    _seed_pending(session, conversation_id)
    state = RuntimeStateService(session)
    state.transition(conversation_id, "waiting")
    state.transition(conversation_id, "executing")
    called = False

    def must_not_generate(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("generation must not run after lifecycle rejection")

    monkeypatch.setattr(generation_module, "generate_paper_from_input", must_not_generate)

    with pytest.raises(GenerationLifecycleError, match="generation_state_conflict"):
        _make_service(session, conversation_id).confirm()

    assert called is False
    assert store.get_generation(conversation_id) is not None
