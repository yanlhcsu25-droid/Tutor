from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import GeneratePaperInput


class RecordingBackend:
    def __init__(self, text="已收到。"):
        self.text = text
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append((messages, tools))
        return {"message": {"content": self.text}}


def _names(request):
    return {
        item["function"]["name"]
        for item in request[1]
        if isinstance(item, dict)
        and isinstance(item.get("function"), dict)
    }


def _system_text(request):
    return "\n".join(
        item.get("content", "")
        for item in request[0]
        if item.get("role") == "system"
    )


def test_runtime_routes_direct_action_before_first_llm_call(session):
    backend = RecordingBackend()

    result = run_teacher_agent(
        session,
        "第三章出10题测试卷",
        conversation_id="runtime-direct-action",
        backend=backend,
    )

    assert result.status == "completed"
    assert len(backend.requests) == 1
    assert "当前任务模式：DIRECT_ACTION" in _system_text(backend.requests[0])
    assert "prepare_generation_plan" in _names(backend.requests[0])


def test_runtime_artifact_request_cannot_end_as_prose_only_advice(session):
    backend = RecordingBackend()

    result = run_teacher_agent(
        session,
        "学生极限一直学不好，帮我安排复习",
        conversation_id="runtime-teaching-planning",
        backend=backend,
    )

    assert result.status == "failed"
    assert "teaching_design_not_created" in result.blocking_errors
    assert "当前任务模式：TEACHING_DESIGN" in _system_text(backend.requests[0])
    names = _names(backend.requests[0])
    assert names == {
        "retrieve_curriculum_candidates",
        "select_teaching_scope",
    }
    assert "prepare_generation_plan" not in names
    assert "confirm_generation" not in names


def test_runtime_pending_state_overrides_task_router(session):
    conversation_id = "runtime-pending-override"
    store = DatabasePendingReplacementStore(session)
    store.set_generation(
        conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["第三章"],
            )
        ),
    )
    backend = RecordingBackend()

    result = run_teacher_agent(
        session,
        "换第二题",
        conversation_id=conversation_id,
        backend=backend,
        state_store=store,
    )

    assert result.status in {"completed", "waiting_confirmation"}
    system = _system_text(backend.requests[0])
    assert '"source": "deterministic_state"' in system
    assert "当前任务模式：DIRECT_ACTION" in system
    assert "confirm_generation" in _names(backend.requests[0])


def test_runtime_current_paper_routes_paper_operation(session):
    backend = RecordingBackend()

    result = run_teacher_agent(
        session,
        "删除第三题",
        conversation_id="runtime-current-paper",
        paper_id="paper-runtime-current",
        version_id="paper-runtime-current",
        backend=backend,
    )

    assert result.status in {"completed", "needs_clarification"}
    assert "当前任务模式：DIRECT_ACTION" in _system_text(backend.requests[0])
    names = _names(backend.requests[0])
    assert "preview_paper_changes" in names
    assert "confirm_paper_changes" in names
