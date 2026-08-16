import json

from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.models import (
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    TeacherAgentRunTrace,
)


class SequenceBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        return self.responses.pop(0)


def tool_call(name: str, arguments: dict | None = None, *, call_id: str = "call-1") -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)},
            }],
        }
    }


def final(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}}


def _question(session, number: int, difficulty: int, knowledge_id: str) -> Question:
    draft = QuestionDraft(
        source_name="autonomous",
        source_item_id=str(number),
        variant=1,
        subject="高数",
        question_type="计算题",
        question_text=f"自主 Agent 真实题干{number}",
        normalized_fingerprint=f"{number:064d}",
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type="计算题",
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add_all([
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge_id,
            relation_type="primary",
        ),
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=difficulty,
            estimated_time_min=5,
            reasoning_depth=1,
            calculation_load=1,
            knowledge_depth=1,
            comprehensive_level=1,
            confidence=1,
            profile_source="human",
            profile_status="approved",
            reason="autonomous test",
        ),
    ])
    return question


def _paper(session) -> Paper:
    node = KnowledgeNode(
        id="autonomous-k",
        node_type="concept",
        name="函数极限",
        normalized_name="函数极限",
    )
    session.add(node)
    session.flush()
    target = _question(session, 1, 4, node.id)
    second = _question(session, 2, 3, node.id)
    third = _question(session, 3, 4, node.id)
    _question(session, 4, 2, node.id)  # eligible easier replacement
    record = PaperBlueprintRecord(
        id="autonomous-bp",
        title="自主测试卷",
        status="draft",
        blueprint_json={"_agent_metadata": {"scope_node_ids": [node.id]}},
    )
    paper = Paper(
        id="autonomous-paper",
        blueprint_id=record.id,
        root_paper_id="autonomous-paper",
        version=1,
        status="draft",
        title="自主测试卷",
        total_score=30,
        validation_status="pending",
    )
    session.add_all([record, paper])
    session.flush()
    for position, question in enumerate([target, second, third], 1):
        session.add(PaperItem(
            paper_id=paper.id,
            question_id=question.id,
            section="计算题",
            position=position,
            score=10,
            locked=False,
        ))
    session.flush()
    return paper


def test_normal_chat_returns_text_without_tool_call(session):
    backend = SequenceBackend(final("你好，需要我帮你组卷、查看试卷，还是调整题目？"))
    result = run_teacher_agent(session, "你好", conversation_id="chat", backend=backend)
    assert result.status == "completed"
    assert "你好" in result.message
    assert len(backend.calls) == 1
    request_messages = backend.calls[0][0]
    assert [item["role"] for item in request_messages] == ["system", "user"]
    assert "当前工作区上下文" in request_messages[0]["content"]
    assert "<current_workspace_state>" in request_messages[-1]["content"]
    assert request_messages[-1]["content"].startswith("你好\n")
    assert not session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "chat"
    )).tool_calls_json


def test_capability_question_is_direct_response_without_router_or_tool(session):
    backend = SequenceBackend(final("我可以组卷、读取和分析当前试卷、预览换题并管理版本。"))
    result = run_teacher_agent(session, "你能帮我做什么？", backend=backend)
    assert result.status == "completed"
    assert "组卷" in result.message
    assert len(backend.calls) == 1


def test_normal_chat_with_paper_uses_validated_no_observation_exit(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("你好，我可以帮你处理当前试卷。"),
        final(json.dumps({
            "paper_observation_required": False,
            "answer": "你好，需要我帮你做什么？",
        }, ensure_ascii=False)),
    )
    result = run_teacher_agent(
        session,
        "你好",
        conversation_id="paper-chat",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert result.message == "你好，需要我帮你做什么？"
    assert len(backend.calls) == 2
    assert backend.calls[1][1] == []
    assert not session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "paper-chat"
    )).tool_calls_json


def test_normal_chat_retries_invalid_grounding_exit_format(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("你好，我可以帮你处理当前试卷。"),
        final("你好，需要我帮你做什么？"),
        final(json.dumps({
            "paper_observation_required": False,
            "answer": "你好，需要我帮你做什么？",
        }, ensure_ascii=False)),
    )
    result = run_teacher_agent(
        session,
        "你好",
        conversation_id="paper-chat-format-retry",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert result.message == "你好，需要我帮你做什么？"
    assert len(backend.calls) == 3
    assert "禁止 Markdown" in backend.calls[2][0][0]["content"]


def test_normal_chat_accepts_fenced_validated_grounding_decision(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("谢谢你。"),
        final(
            "```json\n"
            '{"paper_observation_required":false,"answer":"不客气！"}'
            "\n```"
        ),
    )
    result = run_teacher_agent(
        session,
        "谢谢",
        conversation_id="paper-chat-fenced-decision",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert result.message == "不客气！"
    assert len(backend.calls) == 2


def test_agent_reads_real_question_then_answers_from_observation(session):
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}),
        final("第3题是“自主 Agent 真实题干3”，是一道10分的计算题。"),
    )
    result = run_teacher_agent(
        session, "第3题是什么？", conversation_id="read", paper_id=paper.id,
        version_id=paper.id, backend=backend,
    )
    assert result.status == "completed"
    assert "真实题干3" in result.message
    assert result.paper_read.questions[0].position == 3
    observation = backend.calls[1][0][-1]
    assert observation["role"] == "tool"
    assert "自主 Agent 真实题干3" in observation["content"]
    trace_call = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "read"
    )).tool_calls_json[0]
    assert trace_call["paper_observation"] == {
        "version_id": paper.id,
        "positions": [3],
        "ok": True,
        "code": None,
    }


def test_natural_replacement_reads_then_previews_and_waits_for_confirmation(session):
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}, call_id="read-3"),
        tool_call(
            "preview_replace_question",
            {"position": 3, "difficulty_direction": "easier"},
            call_id="replace-3",
        ),
        final("已找到第3题更简单的替代题，知识范围和题型符合要求。请确认后再替换。"),
    )
    result = run_teacher_agent(
        session, "第3题太难了，换简单一点", conversation_id="replace",
        paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "waiting_confirmation"
    assert result.replacement_preview.ok
    assert result.pending_action.target_position == 3
    assert [call["tool_name"] for call in session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == "replace"
    )).tool_calls_json] == ["read_current_paper", "preview_replace_question"]


def test_non_fixed_wording_preserves_knowledge_without_intent_patterns(session):
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}, call_id="read-natural"),
        tool_call(
            "preview_replace_question",
            {"position": 3, "difficulty_direction": "easier", "preserve_knowledge_points": True},
            call_id="replace-natural",
        ),
        final("找到一道更温和且保持原知识点的候选题，请确认。"),
    )
    result = run_teacher_agent(
        session, "第三道感觉有点折磨学生，给我找个温和一点的，但知识点别动",
        conversation_id="natural", paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "waiting_confirmation"
    assert result.replacement_preview.constraints.knowledge_point_preserved
    preview_schema = next(
        definition["function"]["parameters"]
        for definition in backend.calls[0][1]
        if definition["function"]["name"] == "preview_replace_question"
    )
    assert "Hard constraint" in preview_schema["properties"]["preserve_knowledge_points"]["description"]


def test_multi_tool_read_then_replacement_preview(session):
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}, call_id="multi-read"),
        tool_call(
            "preview_replace_question",
            {"position": 3, "difficulty_direction": "easier"},
            call_id="multi-preview",
        ),
        final("第3题当前难度为4；已找到更简单的候选题，等待你确认。"),
    )
    result = run_teacher_agent(
        session, "先告诉我第3题是什么，再看看有没有更简单的替代题。",
        conversation_id="multi", paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "waiting_confirmation"
    assert len(backend.calls) == 3
    assert result.paper_read and result.replacement_preview


def test_whole_paper_read_then_llm_summary(session):
    paper = _paper(session)
    backend = SequenceBackend(
        tool_call("read_current_paper"),
        final("当前试卷共3道计算题，每题10分，总分30分。"),
    )
    result = run_teacher_agent(
        session, "现在这张卷子整体是什么情况？", conversation_id="overview",
        paper_id=paper.id, version_id=paper.id, backend=backend,
    )
    assert result.status == "completed"
    assert "共3道" in result.message
    assert result.paper_read.paper.question_count == 3


def test_total_score_answer_is_grounded_by_whole_paper_observation(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("当前试卷总分是100分。"),
        final(json.dumps({
            "paper_observation_required": True,
            "answer": "",
        }, ensure_ascii=False)),
        tool_call("read_current_paper", call_id="score-read"),
        final(json.dumps({
            "paper_observation_required": False,
            "answer": "当前试卷总分是30分。",
        }, ensure_ascii=False)),
    )
    result = run_teacher_agent(
        session,
        "这张卷子多少分？",
        conversation_id="paper-total-score",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert result.message == "当前试卷总分是30分。"
    assert result.paper_read.paper.total_score == 30


def test_pending_state_is_rechecked_when_model_only_claims_cancellation(session):
    paper = _paper(session)
    conversation_id = "pending-recheck"
    preview = run_teacher_agent(
        session,
        "第三题简单一点",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            tool_call(
                "preview_replace_question",
                {"position": 3, "difficulty_direction": "easier"},
            ),
            final("已找到方案，请确认。"),
        ),
    )
    assert preview.status == "waiting_confirmation"

    backend = SequenceBackend(
        final("好的，已经取消。"),
        tool_call("cancel_replace_question", call_id="cancel-after-recheck"),
        final("换题方案已取消。"),
    )
    cancelled = run_teacher_agent(
        session,
        "这个方案取消掉",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert cancelled.status == "completed"
    assert len(backend.calls) == 3
    pending_tool_names = [
        item["function"]["name"] for item in backend.calls[0][1]
    ]
    assert pending_tool_names == [
        "confirm_replace_question",
        "cancel_replace_question",
        "read_current_paper",
    ]
    assert [item["role"] for item in backend.calls[1][0][:2]] == ["system", "user"]
    assert "忽略更早对话" in backend.calls[1][0][0]["content"]
    assert backend.calls[1][0][1]["content"].startswith("这个方案取消掉\n")
    assert session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == conversation_id,
        TeacherAgentRunTrace.user_message == "这个方案取消掉",
    )).tool_calls_json[0]["tool_name"] == "cancel_replace_question"


def test_pending_state_claim_is_rejected_when_recheck_still_calls_no_tool(session):
    paper = _paper(session)
    conversation_id = "pending-safe-final"
    preview = run_teacher_agent(
        session,
        "第三题简单一点",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            tool_call(
                "preview_replace_question",
                {"position": 3, "difficulty_direction": "easier"},
            ),
            final("已找到方案，请确认。"),
        ),
    )
    assert preview.status == "waiting_confirmation"

    result = run_teacher_agent(
        session,
        "取消这个方案",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            final("已经取消。"),
            final("已经取消。"),
        ),
    )
    assert result.status == "waiting_confirmation"
    assert "仍未改变" in result.message
    assert "已取消" not in result.message


def test_leaked_tool_protocol_is_retried_instead_of_shown_as_answer(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("我猜第五题是导数题。</think><tool_call>read_current_paper</tool_call>"),
        tool_call("read_current_paper", {"positions": [5]}, call_id="retry-read"),
        final("当前试卷只有3题，没有第5题。"),
    )
    result = run_teacher_agent(
        session,
        "第五题呢？",
        conversation_id="protocol-retry",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "failed"
    assert "导数题" not in result.message
    assert result.paper_read.code == "question_position_not_found"
    assert any(
        "protocol_error" in item.get("content", "")
        for item in backend.calls[1][0]
        if isinstance(item.get("content"), str)
    )


def test_paper_fact_claim_without_tool_is_rechecked(session):
    paper = _paper(session)
    backend = SequenceBackend(
        final("我已读取，第2题是我猜的题目。"),
        final(json.dumps({
            "paper_observation_required": True,
            "answer": "",
        }, ensure_ascii=False)),
        tool_call("read_current_paper", {"positions": [2]}, call_id="fact-recheck"),
        final("第2题是“自主 Agent 真实题干2”。"),
    )
    result = run_teacher_agent(
        session,
        "第2题是什么？",
        conversation_id="paper-fact-recheck",
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert "真实题干2" in result.message
    assert "我猜" not in result.message
    assert result.paper_read.questions[0].position == 2
    assert any(
        "Paper 事实核验" in item.get("content", "")
        for item in backend.calls[1][0]
        if isinstance(item.get("content"), str)
    )


def test_ungrounded_paper_fact_is_rejected_after_focused_recheck(session):
    paper = _paper(session)
    result = run_teacher_agent(
        session,
        "第2题是什么？",
        conversation_id="paper-fact-safe-final",
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            final("第2题是我猜的极限题。"),
            final(json.dumps({
                "paper_observation_required": True,
                "answer": "",
            }, ensure_ascii=False)),
            final("第2题仍然是我猜的极限题。"),
            final("第2题仍然是我猜的极限题。"),
        ),
    )
    assert result.status == "needs_clarification"
    assert "我猜" not in result.message
    assert result.blocking_errors == ["paper_observation_required"]


def test_paper_observation_is_invalidated_after_version_change(session):
    paper = _paper(session)
    conversation_id = "paper-observation-version-change"
    preview = run_teacher_agent(
        session,
        "第三题换简单一点",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            tool_call(
                "preview_replace_question",
                {"position": 3, "difficulty_direction": "easier"},
            ),
            final("已找到方案，请确认。"),
        ),
    )
    assert preview.status == "waiting_confirmation"

    backend = SequenceBackend(
        tool_call("read_current_paper", {"positions": [3]}, call_id="read-old-version"),
        tool_call("confirm_replace_question", call_id="confirm-version-change"),
        final("替换后第3题仍是旧版本题干3。"),
        final(json.dumps({
            "paper_observation_required": True,
            "answer": "",
        }, ensure_ascii=False)),
        tool_call("read_current_paper", {"positions": [3]}, call_id="read-new-version"),
        final("替换后第3题是“自主 Agent 真实题干4”。"),
    )
    result = run_teacher_agent(
        session,
        "确认，并告诉我替换后第3题是什么。",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=backend,
    )
    assert result.status == "completed"
    assert result.replacement.ok
    assert result.message == "替换后第3题是“自主 Agent 真实题干4”。"
    trace = session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == conversation_id,
        TeacherAgentRunTrace.user_message == "确认，并告诉我替换后第3题是什么。",
    ))
    observations = [
        call["paper_observation"]
        for call in trace.tool_calls_json
        if call["tool_name"] == "read_current_paper"
    ]
    assert observations[0]["version_id"] == paper.id
    assert observations[1]["version_id"] == result.replacement.new_version_id
    assert observations[0]["version_id"] != observations[1]["version_id"]


def test_existing_pending_replacement_cannot_be_silently_overwritten(session):
    paper = _paper(session)
    conversation_id = "pending-no-overwrite"
    first = run_teacher_agent(
        session,
        "第三题简单一点",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            tool_call(
                "preview_replace_question",
                {"position": 3, "difficulty_direction": "easier"},
            ),
            final("已有待确认方案。"),
        ),
    )
    original_replacement_id = first.pending_action.replacement_question_id

    second = run_teacher_agent(
        session,
        "这个方案不要了",
        conversation_id=conversation_id,
        paper_id=paper.id,
        version_id=paper.id,
        backend=SequenceBackend(
            tool_call("preview_replace_question", {"position": 3}),
            final("不能覆盖当前方案。"),
        ),
    )
    assert second.status == "waiting_confirmation"
    assert second.blocking_errors == ["pending_replacement_exists"]
    assert second.pending_action is None
    assert session.scalar(select(TeacherAgentRunTrace).where(
        TeacherAgentRunTrace.conversation_id == conversation_id,
        TeacherAgentRunTrace.user_message == "这个方案不要了",
    )).tool_calls_json[0]["result"]["code"] == "pending_replacement_exists"
    assert DatabasePendingReplacementStore(session).get(
        conversation_id
    ).replacement_question_id == original_replacement_id
