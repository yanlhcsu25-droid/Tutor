from __future__ import annotations

import json

from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.models import TeacherAgentRunTrace
from tests.evals.curriculum_fixture import seed_eval_curriculum


class PrepareThenTimeoutBackend:
    # First call creates pending generation; narration call times out.

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1

        if self.calls == 1:
            arguments = {
                "paper_type": "chapter_test",
                "scope_names": ["第一章 函数与极限"],
            }
            return {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-prepare",
                            "type": "function",
                            "function": {
                                "name": "prepare_generation_plan",
                                "arguments": json.dumps(
                                    arguments,
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            }

        raise TimeoutError("The read operation timed out")


def test_post_prepare_narration_timeout_preserves_waiting_state(session) -> None:
    seed_eval_curriculum(session)

    conversation_id = "post-tool-timeout"
    backend = PrepareThenTimeoutBackend()

    result = run_teacher_agent(
        session,
        "帮我出一套高数第一章测试卷。",
        conversation_id=conversation_id,
        backend=backend,
    )

    pending = DatabasePendingReplacementStore(session).get_generation(
        conversation_id
    )

    assert backend.calls == 2
    assert pending is not None
    assert pending.request.paper_type == "chapter_test"
    assert pending.request.question_count == 10
    assert pending.request.total_score == 100
    assert pending.total_score_source == "default_template"

    # Business outcome survives the presentation-layer timeout.
    assert result.status == "waiting_confirmation"
    assert "post_tool_narration_failed" in result.warnings
    assert "agent_execution_failed" not in result.blocking_errors
    assert "方案已成功生成并保存" in result.message

    # Observability is preserved: telemetry still records the timeout.
    trace = session.scalar(
        select(TeacherAgentRunTrace)
        .where(
            TeacherAgentRunTrace.conversation_id == conversation_id
        )
        .order_by(TeacherAgentRunTrace.created_at.desc())
        .limit(1)
    )
    assert trace is not None
    assert trace.result_status == "waiting_confirmation"
    assert trace.error_type == "TimeoutError"
    assert trace.error_stage == "llm_call"
    assert trace.error_message == "The read operation timed out"
