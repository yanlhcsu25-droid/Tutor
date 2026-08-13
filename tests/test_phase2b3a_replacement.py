import json

from sqlalchemy import func, select

from calculus_agent.agent.replacement_parser import parse_replacement_intent
from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.tools.replacement_tools import (
    apply_question_replacement,
    dry_run_replace_question,
)
from calculus_agent.models import (
    CurriculumNode, KnowledgeNode, Paper, PaperBlueprintRecord, PaperItem,
    Question, QuestionDraft, QuestionKnowledgeLink, QuestionProfile,
)


class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def _tool(name: str, arguments: dict | None = None) -> dict:
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


def _final(text: str = "操作已完成。") -> dict:
    return {"message": {"role": "assistant", "content": text}}


def _question(session, number, difficulty, *, question_type="计算题", in_scope=True):
    draft = QuestionDraft(source_name="replacement", source_item_id=str(number), variant=1,
        subject="高等数学", question_type=question_type, question_text=f"题目{number}",
        normalized_fingerprint=f"{number:064d}", status="approved")
    session.add(draft); session.flush()
    question = Question(draft_id=draft.id, question_text=draft.question_text, question_type=question_type,
        verification_status="verified", review_status="approved")
    session.add(question); session.flush()
    knowledge = session.get(KnowledgeNode, "k1" if in_scope else "k2")
    session.add(QuestionKnowledgeLink(question_id=question.id, knowledge_node_id=knowledge.id, relation_type="primary"))
    session.add(QuestionProfile(question_id=question.id, profile_version=1, difficulty=difficulty,
        estimated_time_min=5, reasoning_depth=2, calculation_load=2, knowledge_depth=2,
        comprehensive_level=2, confidence=1, profile_source="human", profile_status="approved", reason="test"))
    session.flush()
    return question


def _paper(session, questions):
    record = PaperBlueprintRecord(id="bp", title="草稿", status="draft", blueprint_json={
        "total_questions": len(questions), "total_score": len(questions) * 10,
        "_agent_metadata": {"source": "teacher_agent", "scope_node_ids": ["k1"]},
    })
    session.add(record); session.flush()
    paper = Paper(id="p1", blueprint_id=record.id, root_paper_id="p1", version=1,
        status="draft", title="草稿", total_score=len(questions) * 10, validation_status="pending")
    session.add(paper); session.flush()
    for position, question in enumerate(questions, 1):
        session.add(PaperItem(paper_id=paper.id, question_id=question.id, section=question.question_type,
            position=position, score=10, locked=False))
    session.flush()
    return paper


def test_replacement_prefers_nearest_easier_and_is_read_only(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    current = _question(session, 1, 4)
    occupied_best = _question(session, 2, 3)
    candidate = _question(session, 3, 3)
    _question(session, 4, 2, in_scope=False)
    paper = _paper(session, [current, occupied_best])
    before = (session.scalar(select(func.count()).select_from(Paper)), session.scalar(select(func.count()).select_from(PaperItem)))
    result = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=parse_replacement_intent("第1题换简单一点"))
    after = (session.scalar(select(func.count()).select_from(Paper)), session.scalar(select(func.count()).select_from(PaperItem)))
    assert result.ok and result.recommended_question.question_id == candidate.id
    assert result.recommended_question.difficulty == 3
    assert result.recommended_question.score == 10
    assert before == after


def test_replacement_rejects_invalid_position_and_boundary(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    paper = _paper(session, [_question(session, 1, 1)])
    assert dry_run_replace_question(session, paper_id="p1", version_id="p1", intent=parse_replacement_intent("第2题换简单一点")).blocking_errors == ["invalid_question_position"]
    assert dry_run_replace_question(session, paper_id="p1", version_id="p1", intent=parse_replacement_intent("第1题换简单一点")).blocking_errors == ["no_easier_candidate"]


def test_replacement_parser_requires_position():
    assert parse_replacement_intent("把这题换简单一点").need_clarification is True


def test_replacement_accepts_existing_question_type_aliases(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    current = _question(session, 1, 3, question_type="选择题")
    candidate = _question(session, 2, 2, question_type="selection")
    paper = _paper(session, [current])
    result = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=parse_replacement_intent("第1题换简单一点"))
    assert result.ok and result.recommended_question.question_id == candidate.id


def test_apply_replacement_creates_child_version_and_preserves_source(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    first = _question(session, 1, 4)
    second = _question(session, 2, 2)
    replacement = _question(session, 3, 3)
    paper = _paper(session, [first, second])
    result = apply_question_replacement(
        session, paper_id=paper.id, source_version_id=paper.id, target_position=1,
        replacement_question_id=replacement.id, difficulty_direction="easier",
    )
    assert result.ok is True
    child = session.get(Paper, result.new_version_id)
    assert child.root_paper_id == paper.id and child.parent_version_id == paper.id
    assert child.version == 2 and child.status == "draft"
    assert [item.question_id for item in session.scalars(select(PaperItem).where(PaperItem.paper_id == paper.id).order_by(PaperItem.position))] == [first.id, second.id]
    child_items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == child.id).order_by(PaperItem.position)))
    assert [item.question_id for item in child_items] == [replacement.id, second.id]
    assert [item.score for item in child_items] == [10, 10]


def test_apply_rejects_stale_version_without_writing(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    current = _question(session, 1, 4); other = _question(session, 2, 2); first_replacement = _question(session, 3, 3); second_replacement = _question(session, 4, 3)
    paper = _paper(session, [current, other])
    applied = apply_question_replacement(session, paper_id="p1", source_version_id="p1", target_position=1, replacement_question_id=first_replacement.id, difficulty_direction="easier")
    count_before = session.scalar(select(func.count()).select_from(Paper))
    stale = apply_question_replacement(session, paper_id="p1", source_version_id="p1", target_position=1, replacement_question_id=second_replacement.id, difficulty_direction="easier")
    assert applied.ok and stale.blocking_errors == ["source_version_not_current"]
    assert session.scalar(select(func.count()).select_from(Paper)) == count_before


def test_conversation_confirmation_applies_once_and_is_isolated(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    current = _question(session, 1, 4); other = _question(session, 2, 2); replacement = _question(session, 3, 3)
    paper = _paper(session, [current, other])
    proposal = run_teacher_agent(
        session,
        "第1题换简单一点",
        conversation_id="a",
        paper_id=paper.id,
        version_id=paper.id,
        backend=_Backend(
            _tool("preview_replace_question", {"position": 1, "difficulty_direction": "easier"}),
            _final("已找到替换方案，请确认。"),
        ),
    )
    assert proposal.status == "waiting_confirmation"
    assert proposal.pending_action.replacement_question_id == replacement.id
    assert run_teacher_agent(
        session,
        "确认",
        conversation_id="b",
        backend=_Backend(_tool("confirm_replace_question"), _final("没有待确认方案。")),
    ).blocking_errors == ["no_pending_action"]
    confirmed = run_teacher_agent(
        session,
        "就用这道",
        conversation_id="a",
        backend=_Backend(_tool("confirm_replace_question"), _final("替换完成。")),
    )
    assert confirmed.status == "completed"
    assert session.scalar(select(func.count()).select_from(Paper)) == 2
    assert run_teacher_agent(
        session,
        "确认",
        conversation_id="a",
        backend=_Backend(_tool("confirm_replace_question"), _final("没有待确认方案。")),
    ).blocking_errors == ["no_pending_action"]
    assert session.scalar(select(func.count()).select_from(Paper)) == 2


def test_conversation_cancel_does_not_create_version(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    paper = _paper(session, [_question(session, 1, 4), _question(session, 2, 2), _question(session, 3, 3)])
    _question(session, 4, 3)
    assert run_teacher_agent(
        session,
        "第1题换简单一点",
        conversation_id="a",
        paper_id=paper.id,
        version_id=paper.id,
        backend=_Backend(
            _tool("preview_replace_question", {"position": 1, "difficulty_direction": "easier"}),
            _final("已找到替换方案，请确认。"),
        ),
    ).status == "waiting_confirmation"
    assert run_teacher_agent(
        session,
        "算了",
        conversation_id="a",
        backend=_Backend(_tool("cancel_replace_question"), _final("已取消。")),
    ).status == "completed"
    assert session.scalar(select(func.count()).select_from(Paper)) == 1


def test_agent_routes_undo_redo_restore(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    original = _question(session, 1, 4); other = _question(session, 2, 2); replacement = _question(session, 3, 3)
    paper = _paper(session, [original, other])
    applied = apply_question_replacement(session, paper_id=paper.id, source_version_id=paper.id, target_position=1, replacement_question_id=replacement.id, difficulty_direction="easier")
    undo = run_teacher_agent(
        session,
        "撤销刚才的修改",
        conversation_id="a",
        paper_id=paper.id,
        version_id=applied.new_version_id,
        backend=_Backend(_tool("undo_paper"), _final("已撤销。")),
    )
    assert undo.status == "completed"
    undone_id = undo.version_operation.current_version_id
    assert session.scalar(select(PaperItem.question_id).where(PaperItem.paper_id == undone_id, PaperItem.position == 1)) == original.id
    redo = run_teacher_agent(
        session,
        "重做",
        conversation_id="a",
        paper_id=paper.id,
        version_id=undone_id,
        backend=_Backend(_tool("redo_paper"), _final("已重做。")),
    )
    assert redo.status == "completed"
    redone_id = redo.version_operation.current_version_id
    assert session.scalar(select(PaperItem.question_id).where(PaperItem.paper_id == redone_id, PaperItem.position == 1)) == replacement.id
    restored = run_teacher_agent(
        session,
        "恢复到版本1",
        conversation_id="a",
        paper_id=paper.id,
        version_id=redone_id,
        backend=_Backend(
            _tool("restore_paper_version", {"target_version": 1}),
            _final("已恢复到版本1。"),
        ),
    )
    assert restored.status == "completed"
    assert session.scalar(select(PaperItem.question_id).where(PaperItem.paper_id == restored.version_operation.current_version_id, PaperItem.position == 1)) == original.id


def test_agent_version_operation_edges(session):
    chapter = CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1)
    session.add_all([chapter, KnowledgeNode(id="k1", node_type="concept", name="章内", normalized_name="章内", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="章外", normalized_name="章外")]); session.flush()
    paper = _paper(session, [_question(session, 1, 3)])
    assert run_teacher_agent(
        session,
        "撤销",
        paper_id=paper.id,
        version_id=paper.id,
        backend=_Backend(_tool("undo_paper"), _final("没有可撤销的版本。")),
    ).blocking_errors == ["nothing_to_undo"]
    assert run_teacher_agent(
        session,
        "重做",
        paper_id=paper.id,
        version_id=paper.id,
        backend=_Backend(_tool("redo_paper"), _final("没有可重做的版本。")),
    ).blocking_errors == ["nothing_to_redo"]
    assert run_teacher_agent(
        session,
        "恢复到版本99",
        paper_id=paper.id,
        version_id=paper.id,
        backend=_Backend(
            _tool("restore_paper_version", {"target_version": 99}),
            _final("找不到版本99。"),
        ),
    ).blocking_errors == ["version_not_found"]
    missing_context = run_teacher_agent(
        session,
        "撤销",
        backend=_Backend(_tool("undo_paper"), _final("当前没有可操作的试卷。")),
    )
    assert missing_context.status == "failed"
    assert missing_context.blocking_errors == ["no_current_paper"]
