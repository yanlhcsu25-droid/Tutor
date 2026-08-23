import hashlib
import json

import pytest

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.identity import DEFAULT_TEACHER_OWNER_KEY
from calculus_agent.runtime import (
    AgentRuntime, PROMPT_ONLY, RuntimeErrorInfo, ToolResult, UserTurn,
)
from calculus_agent.agent.run_tracing import TeacherAgentRunManager
from calculus_agent.models import TeacherAgentRunTrace
from calculus_agent.runtime.tool_execution import ToolExecutor
from calculus_agent.runtime.tool_loop import ToolLoop


def test_agent_runtime_exposes_one_turn_interface(session):
    observed = {}

    def coordinator(_session, message, **kwargs):
        observed.update(message=message, **kwargs)
        return "result"

    runtime = AgentRuntime(
        session,
        coordinator=coordinator,
        backend="backend",
        default_owner_key="teacher",
        max_tool_rounds=4,
    )
    result = runtime.run(UserTurn(
        message="生成第一章试卷",
        conversation_id="conversation",
        operation_id="operation-1",
    ))

    assert result == "result"
    assert observed["message"] == "生成第一章试卷"
    assert observed["owner_key"] == "teacher"
    assert observed["max_tool_rounds"] == 4
    assert observed["operation_id"] == "operation-1"
    assert observed["variant"].name == "state-policy"


def test_runtime_pipeline_documents_program_owned_safety_boundaries():
    source = open("src/calculus_agent/runtime/runtime.py").read()
    assert "model decision" in source
    assert "ToolExecutor" in source
    assert "FinalizationPolicy" in source
    assert "cannot commit lifecycle state" in source


def test_tool_result_and_runtime_error_use_canonical_protocols():
    result = ToolResult.failure("tool_timeout", "工具超时")
    error = RuntimeErrorInfo.from_exception(
        RuntimeError("agent_tool_round_limit"), stage="tool_execution"
    )

    assert result.payload == {
        "ok": False, "code": "tool_timeout", "message": "工具超时",
    }
    assert result.result_fields == {"blocking_errors": ["tool_timeout"]}
    assert error.as_dict() == {
        "error_code": "agent_tool_round_limit",
        "error_type": "RuntimeError",
        "error_message": "agent_tool_round_limit",
        "error_stage": "tool_execution",
    }


def test_runtime_forwards_variant_without_forking_the_agent():
    observed = {}

    def coordinator(_session, message, **kwargs):
        observed.update(kwargs)
        return "result"

    runtime = AgentRuntime(
        object(), coordinator=coordinator, backend="backend",
        default_owner_key="teacher", variant=PROMPT_ONLY,
    )
    runtime.run(UserTurn(message="只给建议"))

    assert observed["variant"] is PROMPT_ONLY
    assert not observed["variant"].tools_enabled
    assert not observed["variant"].persistent_state


def test_malformed_tool_arguments_use_stable_error_code():
    with pytest.raises(ValueError, match="agent_invalid_tool_arguments"):
        ToolLoop.parse_call({
            "function": {"name": "read_paper", "arguments": "{broken"}
        })


def test_tool_executor_deduplicates_successful_mutations():
    class Toolkit:
        calls = 0

        def execute(self, name, arguments):
            self.calls += 1
            return ToolResult(payload={"ok": True}, status="waiting_confirmation")

    toolkit = Toolkit()
    executor = ToolExecutor(toolkit)

    first = executor.execute("create_teaching_design", {"content": {"title": "x"}})
    second = executor.execute("create_teaching_design", {"content": {"title": "x"}})

    assert first is second
    assert toolkit.calls == 1


def test_tool_executor_rolls_back_partial_write_on_exception(session):
    from calculus_agent.models import TeacherAgentConversationMessage

    class FailingToolkit:
        def execute(self, name, arguments):
            session.add(TeacherAgentConversationMessage(
                conversation_id="rollback-boundary", role="assistant", content="partial",
            ))
            session.flush()
            raise TimeoutError("tool_timeout")

    with pytest.raises(TimeoutError, match="tool_timeout"):
        ToolExecutor(FailingToolkit(), session=session).execute(
            "create_teaching_design", {}
        )

    assert session.query(TeacherAgentConversationMessage).filter_by(
        conversation_id="rollback-boundary"
    ).count() == 0


def test_in_progress_operation_fails_closed_without_calling_model(session):
    request = {
        "conversation_id": "conversation",
        "owner_key": DEFAULT_TEACHER_OWNER_KEY,
        "paper_id": None,
        "version_id": None,
        "message": "生成第一章试卷",
    }
    fingerprint = hashlib.sha256(json.dumps(
        request, ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()
    TeacherAgentRunManager(
        session, "conversation", None, request["message"],
        run_id="in-progress-operation", request_fingerprint=fingerprint,
    ).create()

    result = run_teacher_agent(
        session, request["message"], conversation_id="conversation",
        operation_id="in-progress-operation", backend=None,
    )

    assert result.status == "needs_clarification"
    assert result.blocking_errors == ["operation_in_progress"]


def test_operation_claim_is_unique_and_loser_stays_usable(session):
    first = TeacherAgentRunManager(
        session, "conversation", None, "request",
        run_id="shared-operation", request_fingerprint="a" * 64,
    ).create()
    second = TeacherAgentRunManager(
        session, "conversation", None, "request",
        run_id="shared-operation", request_fingerprint="a" * 64,
    ).create()

    assert first.row is not None
    assert second.conflict
    assert session.query(TeacherAgentRunTrace).filter_by(
        run_id="shared-operation"
    ).count() == 1


def test_tool_executor_rejects_noncanonical_results():
    class InvalidToolkit:
        def execute(self, name, arguments):
            return {"ok": True}

    with pytest.raises(TypeError, match="agent_invalid_tool_result"):
        ToolExecutor(InvalidToolkit()).execute("bad_tool", {})
