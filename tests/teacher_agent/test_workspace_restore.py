from __future__ import annotations

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.state import WorkspaceService
from calculus_agent.agent.trace_log import AgentTraceRecorder, read_agent_traces


class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, _messages, _tools):
        return self.responses.pop(0)


def _tool(name: str, arguments: str = "{}") -> dict:
    return {
        "message": {
            "tool_calls": [{
                "id": "restore-test-tool-call",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }]
        }
    }


def _final(message: str) -> dict:
    return {
        "message": {
            "content": message,
        }
    }


def test_workspace_restore_passes_current_paper_to_read_tool(
    session,
    tmp_path,
):
    conversation_id = "workspace-restore-test"

    # 准备 workspace
    WorkspaceService(session).update(
        conversation_id,
        {
            "current_paper_id": "paper-test-001",
            "current_version_id": "paper-test-001",
        },
    )
    WorkspaceService(session).update(
    conversation_id,
    {
        "current_paper_id": "paper-test-001",
        "current_version_id": "paper-test-001",
    },
)

# 不要 commit

    trace_dir = tmp_path / "agent-traces"

    result = run_teacher_agent(
        session,
        "查看当前试卷",
        conversation_id=conversation_id,
        backend=_Backend(
            _tool("read_paper"),
            _final("当前试卷读取完成。"),
        ),
        trace_recorder=AgentTraceRecorder(trace_dir),
    )

    assert result.message == "当前试卷读取完成。"

    trace = read_agent_traces(trace_dir)[0]

    assert trace["paper_id"] == "paper-test-001"

    assert trace["tool_calls"][0]["tool_name"] == "read_paper"

def test_explicit_paper_context_has_priority_over_workspace(
    session,
    tmp_path,
):
    conversation_id = "workspace-priority-test"

    WorkspaceService(session).update(
        conversation_id,
        {
            "current_paper_id": "workspace-paper",
            "current_version_id": "workspace-version",
        },
    )

    trace_dir = tmp_path / "agent-traces"

    run_teacher_agent(
        session,
        "查看当前试卷",
        conversation_id=conversation_id,
        paper_id="explicit-paper",
        version_id="explicit-version",
        backend=_Backend(
            _tool("read_paper"),
            _final("完成"),
        ),
        trace_recorder=AgentTraceRecorder(trace_dir),
    )

    trace = read_agent_traces(trace_dir)[0]

    assert trace["paper_id"] == "explicit-paper"

def test_missing_workspace_does_not_break_agent(
    session,
    tmp_path,
):
    trace_dir = tmp_path / "agent-traces"

    result = run_teacher_agent(
        session,
        "你好",
        conversation_id="no-workspace",
        backend=_Backend(
            _final("你好，我可以帮助组卷。"),
        ),
        trace_recorder=AgentTraceRecorder(trace_dir),
    )

    assert result.status == "completed"
    assert result.message == "你好，我可以帮助组卷。"