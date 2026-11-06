import json

from sqlalchemy import func, select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.tools.read_tools import ReadCurrentPaperInput, read_current_paper
from calculus_agent.models import (
    AgentPendingAdjustment,
    AgentPendingGeneration,
    AgentPendingReplacement,
    Paper,
    PaperItem,
    TeacherAgentRunTrace,
)
from calculus_agent.runtime.contracts import ToolResult
from evaluation.reliability.curriculum_fixture import seed_eval_curriculum
from evaluation.reliability.fixtures.paper import seed_current_paper


ANALYSIS_REQUEST = """请对当前试卷进行只读汇总分析，列出：
1. 试卷总分
2. 总题数
3. 各题型的题目数量
4. 难度分布
5. 知识点分布

不要修改试卷，也不需要读取每道题的完整题干。"""


class SequenceBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return self.responses.pop(0)


def tool_call(name: str, arguments: dict | None = None) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"{name}-call",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments or {}, ensure_ascii=False),
                },
            }],
        }
    }


def final(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}}


def seed_paper(session):
    seed_eval_curriculum(session)
    return seed_current_paper(session, {"question_count": 10, "total_score": 100})


def trace_for(session, conversation_id: str) -> TeacherAgentRunTrace:
    return session.scalars(
        select(TeacherAgentRunTrace).where(
            TeacherAgentRunTrace.conversation_id == conversation_id
        )
    ).one()


def tool_names(trace: TeacherAgentRunTrace) -> list[str]:
    return [call["tool_name"] for call in trace.tool_calls_json]


def analysis_payload(paper_id: str) -> dict:
    return {
        "ok": True,
        "paper_id": paper_id,
        "score_total": 100.0,
        "question_count": 10,
        "question_type_distribution": {"选择题": 4, "填空题": 2, "计算题": 3, "证明题": 1},
        "difficulty_distribution": {"2": 3, "3": 7},
        "knowledge_distribution": {"函数的极限": 6, "极限运算法则": 4},
        "warnings": [],
        "issues": [],
        "blocking_errors": [],
    }


def injector_for(session, version_id: str, analyze_result: ToolResult):
    def inject(name: str, arguments: dict):
        if name == "analyze_paper":
            return analyze_result
        if name == "read_paper":
            result = read_current_paper(
                session,
                current_paper_version_id=version_id,
                request=ReadCurrentPaperInput.model_validate(arguments),
            )
            return ToolResult(
                payload=result.model_dump(mode="json"),
                status="completed" if result.ok else "failed",
                result_fields={
                    "paper_read": result,
                    "blocking_errors": [] if result.ok else [result.code or "paper_read_failed"],
                },
            )
        raise AssertionError(f"unexpected tool: {name}")

    return inject


def test_complete_analysis_evidence_does_not_trigger_read_paper(session):
    context = seed_paper(session)
    backend = SequenceBackend(
        tool_call("analyze_paper"),
        final("总分100分，共10题；题型、难度和知识点分布见分析结果。"),
    )

    result = run_teacher_agent(
        session,
        ANALYSIS_REQUEST,
        conversation_id="analysis-complete",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    trace = trace_for(session, "analysis-complete")
    assert result.status == "completed"
    assert result.analysis is not None and result.analysis.ok
    assert tool_names(trace) == ["analyze_paper"]
    assert len(backend.calls) == 2
    assert all(
        "<paper_read_required" not in str(message.get("content") or "")
        for messages, _ in backend.calls
        for message in messages
    )


def test_incomplete_analysis_requires_additional_current_version_evidence(session):
    context = seed_paper(session)
    payload = analysis_payload(context.version_id)
    payload.pop("question_count")
    analyze_result = ToolResult(payload=payload, status="completed")
    backend = SequenceBackend(
        tool_call("analyze_paper"),
        final("分析完成。"),
        tool_call("read_paper"),
        final("补充读取后完成分析。"),
    )

    result = run_teacher_agent(
        session,
        ANALYSIS_REQUEST,
        conversation_id="analysis-incomplete",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
        tool_fault_injector=injector_for(session, context.version_id, analyze_result),
    )

    assert result.status == "completed"
    assert tool_names(trace_for(session, "analysis-incomplete")) == [
        "analyze_paper",
        "read_paper",
    ]
    assert any(
        "<paper_read_required" in str(message.get("content") or "")
        for message in backend.calls[2][0]
    )


def test_mismatched_analysis_version_is_not_fresh_evidence(session):
    context = seed_paper(session)
    analyze_result = ToolResult(
        payload=analysis_payload("old-paper-version"),
        status="completed",
    )
    backend = SequenceBackend(
        tool_call("analyze_paper"),
        final("旧版本分析完成。"),
        tool_call("read_paper"),
        final("当前版本读取完成。"),
    )

    result = run_teacher_agent(
        session,
        ANALYSIS_REQUEST,
        conversation_id="analysis-version-mismatch",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
        tool_fault_injector=injector_for(session, context.version_id, analyze_result),
    )

    assert result.status != "completed"
    assert "paper_observation_required" in result.blocking_errors
    assert tool_names(trace_for(session, "analysis-version-mismatch")) == [
        "analyze_paper",
        "read_paper",
    ]


def test_failed_analysis_cannot_claim_success(session):
    context = seed_paper(session)
    analyze_result = ToolResult.failure("analysis_failed", "分析失败。")
    backend = SequenceBackend(
        tool_call("analyze_paper"),
        final("分析完成。"),
        tool_call("read_paper"),
        final("读取完成。"),
    )

    result = run_teacher_agent(
        session,
        ANALYSIS_REQUEST,
        conversation_id="analysis-failed",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
        tool_fault_injector=injector_for(session, context.version_id, analyze_result),
    )

    assert result.status != "completed"
    assert "analysis_failed" in result.blocking_errors
    assert result.message != "分析完成。"
    assert tool_names(trace_for(session, "analysis-failed")) == [
        "analyze_paper",
        "read_paper",
    ]


def test_read_only_analysis_does_not_pollute_generation_or_paper_state(session):
    context = seed_paper(session)
    conversation_id = "analysis-state-isolation"
    paper_state_before = list(session.execute(
        select(Paper.id, Paper.version, Paper.status, Paper.total_score).order_by(Paper.id)
    ))
    item_state_before = list(session.execute(
        select(PaperItem.id, PaperItem.paper_id, PaperItem.position, PaperItem.score)
        .order_by(PaperItem.id)
    ))
    backend = SequenceBackend(
        tool_call("analyze_paper"),
        final("总分100分，共10题。"),
    )

    result = run_teacher_agent(
        session,
        ANALYSIS_REQUEST,
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    first_prompt = "\n".join(
        str(message.get("content") or "")
        for message in backend.calls[0][0]
    )
    assert result.status == "completed"
    assert '"question_count":4' not in first_prompt
    assert '"question_count": 4' not in first_prompt
    assert session.scalar(select(func.count()).select_from(AgentPendingGeneration)) == 0
    assert session.scalar(select(func.count()).select_from(AgentPendingReplacement)) == 0
    assert session.scalar(select(func.count()).select_from(AgentPendingAdjustment)) == 0
    assert list(session.execute(
        select(Paper.id, Paper.version, Paper.status, Paper.total_score).order_by(Paper.id)
    )) == paper_state_before
    assert list(session.execute(
        select(PaperItem.id, PaperItem.paper_id, PaperItem.position, PaperItem.score)
        .order_by(PaperItem.id)
    )) == item_state_before
