from calculus_agent.agent.agent import _merge_result_fields, run_teacher_agent


class RepeatingInvalidArgumentsBackend:
    def __init__(self):
        self.calls = 0

    def complete(self, _messages, _tools):
        self.calls += 1
        return {
            "message": {
                "tool_calls": [{
                    "id": f"invalid-{self.calls}",
                    "function": {
                        "name": "prepare_teaching_planning_draft",
                        "arguments": {"draft": "not-json"},
                    },
                }]
            }
        }


class RepeatingInspectionBackend:
    """Would loop forever without the runtime clarification boundary."""

    def __init__(self):
        self.calls = 0

    def complete(self, _messages, _tools):
        self.calls += 1
        return {
            "message": {
                "tool_calls": [
                    {
                        "id": f"inspect-{self.calls}",
                        "type": "function",
                        "function": {
                            "name": "inspect_curriculum",
                            "arguments": (
                                '{"scope_names":["极限","无穷小","极限运算"]}'
                            ),
                        },
                    }
                ]
            }
        }


def test_repeated_same_tool_validation_failure_stops_after_one_retry(session):
    backend = RepeatingInvalidArgumentsBackend()

    result = run_teacher_agent(
        session,
        "学生极限不好，帮我设计复习方案。",
        conversation_id="repeated-invalid-arguments",
        backend=backend,
    )

    assert backend.calls == 2
    assert result.status == "failed"
    assert result.blocking_errors == ["invalid_tool_arguments"]
    assert "agent_tool_round_limit" not in result.blocking_errors


def test_result_field_merge_deduplicates_repeated_blocking_errors():
    target = {"blocking_errors": ["existing"]}

    _merge_result_fields(
        target,
        {"blocking_errors": ["existing", "repeated", "repeated"]},
    )

    assert target["blocking_errors"] == ["existing", "repeated"]


def test_needs_clarification_tool_result_stops_agent_loop(session):
    backend = RepeatingInspectionBackend()

    result = run_teacher_agent(
        session,
        "学生最近学习极限比较困难，尤其是无穷小和极限运算。",
        conversation_id="clarification-boundary",
        backend=backend,
    )

    assert backend.calls == 1
    assert result.status == "needs_clarification"
    assert result.blocking_errors == ["scope_resolution_required"]
    assert result.clarification_questions == ["请先确认教材章节或知识点范围；我会先根据候选教材范围完成解析。"]
    assert "请先确认教材章节或知识点范围" in result.message
