import json

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


class StructuredRecordingBackend(RecordingBackend):
    def __init__(self, route=None, *, route_error=None, text="已收到。"):
        super().__init__(text)
        self.route = route
        self.route_error = route_error
        self.routing_requests = []

    def complete_structured(self, messages, schema):
        self.routing_requests.append((messages, schema))
        if self.route_error is not None:
            raise self.route_error
        content = self.route if isinstance(self.route, str) else json.dumps(self.route)
        return {"message": {"content": content}}


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


def test_runtime_semantic_route_drives_tool_surface(session):
    backend = StructuredRecordingBackend({
        "task_type": "TEACHING_PLANNING",
        "confidence": 0.94,
        "artifact_required": False,
        "clarification_needed": False,
        "reason": "learning problem with generation explicitly declined",
    })

    run_teacher_agent(
        session,
        "学生总丢分，但先别出卷。",
        conversation_id="runtime-semantic-route",
        backend=backend,
    )

    assert len(backend.routing_requests) == 1
    assert "当前任务模式：TEACHING_PLANNING" in _system_text(backend.requests[0])
    assert '"source": "llm_router"' in _system_text(backend.requests[0])
    assert "prepare_generation_plan" not in _names(backend.requests[0])


def test_runtime_deterministic_override_skips_semantic_router(session):
    backend = StructuredRecordingBackend(route_error=AssertionError("must not route"))

    run_teacher_agent(
        session,
        "第三章出10题测试卷",
        conversation_id="runtime-deterministic-first",
        backend=backend,
    )

    assert backend.routing_requests == []
    assert "当前任务模式：DIRECT_ACTION" in _system_text(backend.requests[0])


def test_runtime_invalid_semantic_route_falls_back_without_crashing(session):
    backend = StructuredRecordingBackend("not-json")

    run_teacher_agent(
        session,
        "为什么洛必达法则不能随便用？",
        conversation_id="runtime-route-fallback",
        backend=backend,
    )

    assert len(backend.routing_requests) == 1
    assert '"source": "heuristic_fallback"' in _system_text(backend.requests[0])
    assert "当前任务模式：INFORMATION_REQUEST" in _system_text(backend.requests[0])


def test_runtime_ambiguous_semantic_route_exposes_no_tools(session):
    backend = StructuredRecordingBackend({
        "task_type": "TEACHING_PLANNING",
        "confidence": 0.6,
        "artifact_required": False,
        "clarification_needed": True,
        "clarification_question": "您希望先讨论复习思路，还是直接生成练习？",
        "reason": "both planning and generation are plausible",
    })

    run_teacher_agent(
        session,
        "期中前想针对一下。",
        conversation_id="runtime-route-clarification",
        backend=backend,
    )

    assert backend.requests[0][1] == []
    assert "不要调用会改变业务状态的 Tool" in _system_text(backend.requests[0])


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
