import calculus_agent.api as api
from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.trace_log import (
    AgentTraceRecorder,
    list_agent_trace_sessions,
    list_agent_trace_turns,
    read_agent_traces,
)


class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, _messages, _tools):
        return self.responses.pop(0)


def _tool(name: str, arguments: str = "{}") -> dict:
    return {
        "message": {
            "tool_calls": [{
                "id": "trace-tool-call",
                "function": {"name": name, "arguments": arguments},
            }]
        }
    }


def _final(message: str) -> dict:
    return {"message": {"content": message}}


def test_trace_records_real_tool_order_memory_and_final_response(session, tmp_path):
    directory = tmp_path / "agent-traces"
    result = run_teacher_agent(
        session,
        "帮我出一套题",
        conversation_id="trace-conversation",
        backend=_Backend(
            _tool("preview_generation_plan"),
            _final("请先确认组卷范围。"),
        ),
        trace_recorder=AgentTraceRecorder(directory),
    )

    assert result.status == "needs_clarification"
    trace = read_agent_traces(directory)[0]
    assert trace["status"] == "success"
    assert trace["agent_status"] == "needs_clarification"
    assert trace["user_input"] == "帮我出一套题"
    assert trace["final_response"] == "请先确认组卷范围。"
    assert trace["duration_ms"] >= 0
    assert trace["memory_before"]["active_task"] == {}
    assert [item["tool_name"] for item in trace["tool_calls"]] == [
        "preview_generation_plan"
    ]
    tool = trace["tool_calls"][0]
    assert tool["arguments"] == {}
    assert tool["memory_before"]["active_task"] == {}
    assert tool["memory_after"]["active_task"]["type"] == "generation"
    assert trace["memory_after"] == tool["memory_after"]
    assert list_agent_trace_sessions(directory) == [{
        "conversation_id": "trace-conversation",
        "started_at": trace["started_at"],
        "last_user_input": "帮我出一套题",
        "status": "success",
        "turn_count": 1,
    }]
    assert list_agent_trace_turns("trace-conversation", directory) == [trace]


def test_trace_redacts_secrets_and_write_failure_does_not_break_agent(session, tmp_path):
    directory = tmp_path / "agent-traces"
    recorder = AgentTraceRecorder(directory)
    recorder.start(
        conversation_id="redaction",
        paper_id=None,
        user_input="token=secret-value api_key: another-secret password is hidden 密码是中文密钥 sk-secretkey123456",
    )
    recorder.record_tool_call(
        tool_name="test_tool",
        arguments={"password": "hidden", "safe": "visible"},
        memory_before={},
        result={"authorization": "Bearer hidden"},
        memory_after={},
    )
    recorder.finish(
        agent_status="completed",
        final_response="完成",
        memory_after={},
        paper_id=None,
    )
    raw = next(directory.glob("*.jsonl")).read_text(encoding="utf-8")
    assert "secret-value" not in raw
    assert "another-secret" not in raw
    assert "hidden" not in raw
    assert "中文密钥" not in raw
    assert "[REDACTED]" in raw

    blocked_directory = tmp_path / "not-a-directory"
    blocked_directory.write_text("block", encoding="utf-8")
    result = run_teacher_agent(
        session,
        "你好",
        conversation_id="trace-write-failure",
        backend=_Backend(_final("你好，我可以协助组卷。")),
        trace_recorder=AgentTraceRecorder(blocked_directory),
    )
    assert result.status == "completed"
    assert result.message == "你好，我可以协助组卷。"


def test_trace_keeps_multiple_tool_calls_in_execution_order(session, tmp_path):
    directory = tmp_path / "agent-traces"
    result = run_teacher_agent(
        session,
        "先生成方案，再读取当前试卷",
        conversation_id="multiple-tools",
        backend=_Backend(
            {"message": {"tool_calls": [
                {"id": "first", "function": {"name": "preview_generation_plan", "arguments": "{}"}},
                {"id": "second", "function": {"name": "read_current_paper", "arguments": "{}"}},
            ]}},
            _final("已处理。"),
        ),
        trace_recorder=AgentTraceRecorder(directory),
    )

    assert result.status == "needs_clarification"
    calls = read_agent_traces(directory)[0]["tool_calls"]
    assert [(item["sequence"], item["tool_name"]) for item in calls] == [
        (1, "preview_generation_plan"),
        (2, "read_current_paper"),
    ]
    assert calls[1]["memory_before"] == calls[0]["memory_after"]


def test_admin_trace_read_apis_are_read_only_adapters(monkeypatch):
    sessions = [{"conversation_id": "c1", "turn_count": 2}]
    turns = [{"trace_id": "t1", "conversation_id": "c1"}]
    monkeypatch.setattr(api, "list_agent_trace_sessions", lambda: sessions)
    monkeypatch.setattr(api, "list_agent_trace_turns", lambda conversation_id: turns if conversation_id == "c1" else [])

    assert api.get_agent_trace_sessions(limit=10) == {"items": sessions}
    assert api.get_agent_trace_turns(conversation_id="c1", limit=10) == {"items": turns}
