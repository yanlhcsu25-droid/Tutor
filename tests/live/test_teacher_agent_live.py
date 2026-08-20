import json
import os
from uuid import uuid4

import httpx
import pytest


BASE_URL = os.getenv(
    "TEACHER_AGENT_BASE_URL",
    "http://127.0.0.1:8000",
)

# 防止普通 uv run pytest 时误调用真实模型。
if os.getenv("RUN_LIVE_LLM") != "1":
    pytest.skip(
        "Live LLM tests disabled. Set RUN_LIVE_LLM=1 to run.",
        allow_module_level=True,
    )


INTERNAL_LEAK_MARKERS = [
    "CurriculumNode",
    "KnowledgeNode",
    "selected_node_ids",
    "scope_level",
    "teaching_scope_unsupported_node_type",
    "agent_tool_round_limit",
    "validator",
]


def _conversation_id() -> str:
    return f"live-test-{uuid4().hex[:12]}"


def _run_turn(
    client: httpx.Client,
    *,
    conversation_id: str,
    message: str,
) -> tuple[dict, dict]:
    response = client.post(
        f"{BASE_URL}/api/v1/teacher-agent/run",
        json={
            "message": message,
            "conversation_id": conversation_id,
        },
    )

    assert response.status_code == 200, response.text

    result = response.json()

    run_id = result.get("run_id")
    assert run_id, f"missing run_id: {result}"

    trace_response = client.get(
        f"{BASE_URL}/api/v1/teacher-agent/runs/{run_id}"
    )
    assert trace_response.status_code == 200, trace_response.text

    trace = trace_response.json()

    print("\n--- Teacher Agent Live Turn ---")
    print("USER:", message)
    print("STATUS:", result.get("status"))
    print("MESSAGE:", (result.get("message") or "")[:240])
    print("TOOLS:", _tool_names(trace))
    print("RUN_ID:", run_id)
    if trace.get("error_code") or trace.get("error_stage"):
        print(
            "ERROR:",
            trace.get("error_code"),
            trace.get("error_stage"),
            (trace.get("error_message") or "")[:240],
        )
    print("LAST SPANS:")
    for span in trace.get("spans", [])[-8:]:
        if span.get("span_type") not in {"model_call", "tool_call"}:
            continue
        output = span.get("output_json") or {}
        summary = (
            f"tool_calls={output.get('tool_calls')}"
            if span.get("span_type") == "model_call"
            else ", ".join(
                f"{key}={value}"
                for key, value in output.items()
                if key in {"ok", "confirmed", "cancelled", "code", "status"}
            )
        )
        print(span.get("name"), "|", span.get("status"), "|", summary)

    return result, trace


def _tool_names(trace: dict) -> list[str]:
    names = []

    for span in trace.get("spans", []):
        if span.get("span_type") != "tool_call":
            continue
        name = span.get("name")
        if name and name not in names:
            names.append(name)

    return names


def _assert_no_internal_leak(result: dict) -> None:
    visible = json.dumps(
        {
            "message": result.get("message"),
            "clarification_questions": result.get(
                "clarification_questions", []
            ),
            "blocking_errors": result.get("blocking_errors", []),
        },
        ensure_ascii=False,
    )

    for marker in INTERNAL_LEAK_MARKERS:
        assert marker not in visible, (
            f"internal implementation leaked to user: {marker}\n"
            f"{visible}"
        )


def _assert_not_runtime_failure(result: dict, trace: dict) -> None:
    assert result.get("status") != "failed", result

    assert trace.get("error_code") != "agent_tool_round_limit", trace

    message = result.get("message") or ""
    assert "Teacher Agent 暂时无法完成这次请求" not in message

    _assert_no_internal_leak(result)


@pytest.fixture
def client():
    with httpx.Client(timeout=180.0) as client:
        health = client.get(f"{BASE_URL}/api/v1/health")
        assert health.status_code == 200, (
            f"Backend is not running at {BASE_URL}"
        )
        yield client


def test_live_teaching_planning_scope_followup(client):
    """
    Live E2E：

    第一轮：
        学生极限不好，帮我设计复习方案。
        → 语义映射课程范围
        → 创建 TeachingDesign
        → awaiting_confirmation

    第二轮：
        高数第一章，基础一点。
        → 必须理解为修改当前 TeachingDesign
        → revise_teaching_design
        → 创建新版本
        → 继续 awaiting_confirmation

    重点防回归：
    - 不允许第二轮误 confirm；
    - 不允许第二轮直接生成 Paper；
    - 不允许重新掉进旧 scope resolver；
    - 不允许仅凭 status != failed 就判 PASS。
    """

    conversation_id = _conversation_id()

    # ------------------------------------------------------------------
    # Turn 1: semantic teaching-planning request
    # ------------------------------------------------------------------
    first, first_trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="学生极限不好，帮我设计复习方案。",
    )

    _assert_not_runtime_failure(first, first_trace)

    first_tools = _tool_names(first_trace)

    assert first.get("status") == "waiting_confirmation", first

    assert "retrieve_curriculum_candidates" in first_tools, first_tools
    assert "select_teaching_scope" in first_tools, first_tools
    assert "inspect_curriculum" in first_tools, first_tools
    assert "inspect_question_bank" in first_tools, first_tools
    assert "create_teaching_design" in first_tools, first_tools

    assert "confirm_teaching_design" not in first_tools, first_tools
    assert "revise_teaching_design" not in first_tools, first_tools
    assert "confirm_generation" not in first_tools, first_tools

    first_design = first.get("teaching_design")
    assert first_design, first

    assert first_design.get("status") == "awaiting_confirmation", first_design

    first_version_id = first_design.get("version_id")
    assert first_version_id, first_design

    first_scope_names = (
        first_design.get("content", {}).get("scope_names") or []
    )
    assert any(
        "第一章" in name or "函数与极限" in name
        for name in first_scope_names
    ), first_scope_names

    # 第一轮只生成待确认设计，不应该已经生成 Paper。
    assert first.get("paper") is None, first
    assert first.get("teaching_design_generation") is None, first

    # ------------------------------------------------------------------
    # Turn 2: modify pending TeachingDesign
    # ------------------------------------------------------------------
    second, second_trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="高数第一章，基础一点。",
    )

    _assert_not_runtime_failure(second, second_trace)

    second_tools = _tool_names(second_trace)

    # 这是本测试最重要的断言：
    # “基础一点”是 revision，不是 confirmation。
    assert "revise_teaching_design" in second_tools, second_tools

    assert "confirm_teaching_design" not in second_tools, second_tools
    assert "confirm_generation" not in second_tools, second_tools
    assert "prepare_generation_plan" not in second_tools, second_tools

    assert second.get("status") == "waiting_confirmation", second

    second_design = second.get("teaching_design")
    assert second_design, second

    assert (
        second_design.get("status") == "awaiting_confirmation"
    ), second_design

    second_version_id = second_design.get("version_id")
    assert second_version_id, second_design

    # revise 必须真正生成新版本，不能原地修改。
    assert second_version_id != first_version_id, {
        "first_version_id": first_version_id,
        "second_version_id": second_version_id,
    }

    # 如果当前 TeachingDesign schema 暴露 parent_version_id，
    # 则新版本应明确基于上一版本。
    parent_version_id = second_design.get("parent_version_id")
    if parent_version_id is not None:
        assert parent_version_id == first_version_id, {
            "first_version_id": first_version_id,
            "parent_version_id": parent_version_id,
        }

    second_scope_names = (
        second_design.get("content", {}).get("scope_names") or []
    )

    assert any(
        "第一章" in name or "函数与极限" in name
        for name in second_scope_names
    ), second_scope_names

    # 修改 TeachingDesign 以后仍然只是等待教师确认，
    # 绝不能在同一轮偷偷生成 Paper。
    assert second.get("paper") is None, second
    assert second.get("teaching_design_generation") is None, second

    # 防止旧 resolver / scope boundary 回归。
    trace_text = json.dumps(
        second_trace,
        ensure_ascii=False,
        default=str,
    )

    assert "scope_not_found" not in trace_text
    assert "curriculum_scope_unresolved" not in trace_text
    assert "teaching_scope_unsupported_node_type" not in trace_text
    assert "agent_tool_round_limit" not in trace_text
    assert "invalid_tool_arguments" not in trace_text

def test_live_direct_generation_with_chapter(client):
    """
    明确章节属于 deterministic-first 场景。

    “第一章”应该直接解析成 canonical scope，
    不应该要求用户说完整数据库名称。
    """
    conversation_id = _conversation_id()

    result, trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="帮我出一套第一章的基础练习卷。",
    )

    _assert_not_runtime_failure(result, trace)

    trace_text = json.dumps(trace, ensure_ascii=False)

    assert (
        "函数与极限" in trace_text
        or "第一章" in trace_text
        or "第1章" in trace_text
    )

    assert "scope_not_found" not in trace_text


def test_live_semantic_scope_with_limit_paraphrase(client):
    """
    验证真正的 Semantic Retrieval：

    用户完全不说“极限”，只说“x趋近0时函数值怎么变化”。
    """
    conversation_id = _conversation_id()

    result, trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message=(
            "学生不理解 x 趋近 0 时函数值怎么变化，"
            "帮我设计一个复习方案。"
        ),
    )

    _assert_not_runtime_failure(result, trace)

    tools = _tool_names(trace)
    trace_text = json.dumps(trace, ensure_ascii=False)

    assert "retrieve_curriculum_candidates" in tools, tools

    # 不要求 embedding Top1 必须固定，
    # 但正确的极限相关语义必须进入整个 Agent 上下文。
    assert any(
        keyword in trace_text
        for keyword in [
            "函数的极限",
            "函数极限",
            "极限",
            "函数与极限",
        ]
    ), trace_text


def test_live_semantic_direct_generation(client):
    """
    验证 Direct Generation 不能只有 deterministic resolver。

    “极限相关”这种自然语言 scope 应能够进入统一 Scope Pipeline，
    而不是直接 scope_not_found。
    """
    conversation_id = _conversation_id()

    result, trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="帮我出一套极限相关的基础练习卷。",
    )

    _assert_not_runtime_failure(result, trace)

    trace_text = json.dumps(trace, ensure_ascii=False)

    assert "scope_not_found" not in trace_text
    assert "curriculum_scope_unresolved" not in trace_text

    tools = _tool_names(trace)

    assert (
        "prepare_generation_plan" in tools
        or result.get("status") == "needs_clarification"
        or result.get("status") == "waiting_confirmation"
    ), {
        "status": result.get("status"),
        "tools": tools,
        "message": result.get("message"),
    }


def _tool_outputs(trace, tool_name):
    spans = trace.get("spans", []) if isinstance(trace, dict) else trace

    return [
        span.get("output_json")
        for span in spans
        if span.get("span_type") == "tool_call"
        and span.get("name") == tool_name
        and span.get("output_json") is not None
    ]


def _last_active_teaching_design(trace):
    spans = trace.get("spans", []) if isinstance(trace, dict) else trace

    for span in reversed(spans):
        if not span.get("name", "").endswith("_state_change"):
            continue

        if span.get("span_type") != "state_transition":
            continue
        output = span.get("output_json") or {}
        after = output.get("after") or {}

        if "active_teaching_design" in after:
            return after.get("active_teaching_design")

    return None


def test_live_pending_teaching_design_confirm(client):
    """
    Live E2E:

    第一轮：
    学生极限不好
    → create_teaching_design
    → awaiting_confirmation

    第二轮：
    确认，就按这个
    → confirm_teaching_design

    防止确认被误判成 revise / cancel。
    """

    conversation_id = _conversation_id()

    # =========================
    # Turn 1: create design
    # =========================
    first, first_trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="学生极限不好，帮我设计复习方案。",
    )

    _assert_not_runtime_failure(first, first_trace)

    first_tools = _tool_names(first_trace)

    assert first.get("status") == "waiting_confirmation", first
    assert "create_teaching_design" in first_tools, first_tools

    first_design = first.get("teaching_design")
    assert first_design is not None, first
    assert first_design.get("status") == "awaiting_confirmation", first_design

    first_version_id = first_design.get("version_id")
    assert first_version_id, first_design

    # =========================
    # Turn 2: confirm
    # =========================
    second, second_trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="确认，就按这个。",
    )

    _assert_not_runtime_failure(second, second_trace)

    second_tools = _tool_names(second_trace)

    # 核心 intent / tool surface 断言
    assert "confirm_teaching_design" in second_tools, second_tools
    assert "revise_teaching_design" not in second_tools, second_tools
    assert "discard_teaching_design" not in second_tools, second_tools

    confirm_outputs = _tool_outputs(
        second_trace,
        "confirm_teaching_design",
    )

    assert confirm_outputs, second_trace

    confirm_output = confirm_outputs[-1]

    # confirm Tool 必须真正确认同一个 TeachingDesign
    assert confirm_output.get("confirmed") is True, confirm_output

    confirmed_design = confirm_output.get("teaching_design")
    assert confirmed_design is not None, confirm_output

    assert confirmed_design.get("version_id") == first_version_id, {
        "first_version_id": first_version_id,
        "confirmed_design": confirmed_design,
    }

    assert confirmed_design.get("status") == "confirmed", confirmed_design

    # 不允许确认时偷偷创建新版 TeachingDesign
    assert "create_teaching_design" not in second_tools, second_tools

    trace_text = json.dumps(
        second_trace,
        ensure_ascii=False,
        default=str,
    )

    assert "invalid_tool_arguments" not in trace_text
    assert "agent_tool_round_limit" not in trace_text


def test_live_pending_teaching_design_cancel(client):
    """
    Live E2E:

    第一轮：
    学生极限不好
    → create_teaching_design
    → awaiting_confirmation

    第二轮：
    算了，这个方案不要了
    → discard_teaching_design
    → active design cleared
    → 不确认、不修改、不生成 Paper
    """

    conversation_id = _conversation_id()

    # =========================
    # Turn 1: create design
    # =========================
    first, first_trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="学生极限不好，帮我设计复习方案。",
    )

    _assert_not_runtime_failure(first, first_trace)

    first_tools = _tool_names(first_trace)

    assert first.get("status") == "waiting_confirmation", first
    assert "create_teaching_design" in first_tools, first_tools

    first_design = first.get("teaching_design")
    assert first_design is not None, first

    assert first_design.get("status") == "awaiting_confirmation", first_design

    first_version_id = first_design.get("version_id")
    assert first_version_id, first_design

    # =========================
    # Turn 2: cancel
    # =========================
    second, second_trace = _run_turn(
        client,
        conversation_id=conversation_id,
        message="算了，这个方案不要了。",
    )

    _assert_not_runtime_failure(second, second_trace)

    second_tools = _tool_names(second_trace)

    # 核心 intent / tool surface 断言
    assert "discard_teaching_design" in second_tools, second_tools

    assert "confirm_teaching_design" not in second_tools, second_tools
    assert "revise_teaching_design" not in second_tools, second_tools
    assert "confirm_generation" not in second_tools, second_tools
    assert "prepare_generation_plan" not in second_tools, second_tools

    discard_outputs = _tool_outputs(
        second_trace,
        "discard_teaching_design",
    )

    assert discard_outputs, second_trace

    discard_output = discard_outputs[-1]

    assert discard_output.get("ok") is True, discard_output

    discarded_design = discard_output.get("teaching_design")

    if discarded_design is not None:
        assert discarded_design.get("version_id") == first_version_id, {
            "first_version_id": first_version_id,
            "discarded_design": discarded_design,
        }

        assert discarded_design.get("status") == "superseded", (
            discarded_design
        )

    # 最重要：取消以后 active design 必须真的被清空
    active_design = _last_active_teaching_design(second_trace)

    assert active_design is None, {
        "active_teaching_design": active_design,
        "trace": second_trace,
    }

    # Working Memory 也必须进入 cancelled
    trace_text = json.dumps(
        second_trace,
        ensure_ascii=False,
        default=str,
    )

    assert '"status": "cancelled"' in trace_text or "'status': 'cancelled'" in trace_text

    # 不允许出现旧 runtime 问题
    assert "invalid_tool_arguments" not in trace_text
    assert "agent_tool_round_limit" not in trace_text