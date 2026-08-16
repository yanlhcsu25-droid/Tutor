from sqlalchemy import func, select
import json
import pytest

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.tool_registry import PreviewAdjustmentInput
from calculus_agent.agent.schemas import ReplacementIntent
from calculus_agent.agent.tools.analysis_tools import confirm_adjust_paper, preview_adjust_paper, validate_adjustment_plan_freshness
from calculus_agent.agent.tools.replacement_tools import dry_run_replace_question, replacement_requires_target_knowledge
from calculus_agent.models import CurriculumNode, KnowledgeNode, Paper, PaperBlueprintRecord, PaperItem, Question, QuestionDraft, QuestionKnowledgeLink, QuestionProfile
from calculus_agent.papers.workflow import _clone_version, undo_paper_operations


def _question(session, number, difficulty, knowledge_id, question_type="选择题"):
    draft = QuestionDraft(source_name="c2", source_item_id=str(number), variant=1, subject="高数", question_type=question_type, question_text=str(number), normalized_fingerprint=f"{number:064d}", status="approved")
    session.add(draft)
    session.flush()
    question = Question(draft_id=draft.id, question_text=str(number), question_type=question_type, solution_json={"solution_steps": ["解析"]}, verification_status="verified", review_status="approved")
    session.add(question)
    session.flush()
    session.add(QuestionKnowledgeLink(question_id=question.id, knowledge_node_id=knowledge_id, relation_type="primary"))
    session.add(QuestionProfile(question_id=question.id, profile_version=1, difficulty=difficulty, estimated_time_min=5, reasoning_depth=1, calculation_load=1, knowledge_depth=1, comprehensive_level=1, confidence=1, profile_source="human", profile_status="approved", reason="test"))
    session.flush()
    return question


def _paper(session, questions):
    record = PaperBlueprintRecord(id="bp-c2", title="测试", status="draft", blueprint_json={"title": "测试", "total_questions": len(questions), "total_score": len(questions) * 5, "sections": [{"question_type": "选择题", "count": len(questions), "score_per_question": 5, "total_score": len(questions) * 5}], "question_type_counts": {"选择题": len(questions)}, "_agent_metadata": {"scope_node_ids": ["k1", "k2", "k3"]}})
    session.add(record)
    session.flush()
    paper = Paper(id="p-c2", blueprint_id=record.id, root_paper_id="p-c2", version=1, status="draft", title="测试", total_score=len(questions) * 5, validation_status="pending")
    session.add(paper)
    session.flush()
    for position, question in enumerate(questions, 1):
        session.add(PaperItem(paper_id=paper.id, question_id=question.id, section=question.question_type, position=position, score=5, locked=False))
    session.flush()
    return paper


def _seed(session):
    session.add(CurriculumNode(id="c1", node_type="chapter", code="1", title="第一章", sort_order=1))
    session.add_all([KnowledgeNode(id="k1", node_type="concept", name="当前", normalized_name="当前", curriculum_node_id="c1"), KnowledgeNode(id="k2", node_type="concept", name="相似", normalized_name="相似", curriculum_node_id="c1"), KnowledgeNode(id="k3", node_type="concept", name="目标", normalized_name="目标", curriculum_node_id="c1")])
    session.flush()
    target = _question(session, 1, 2, "k1")
    reference = _question(session, 2, 2, "k2")
    blocked = _question(session, 3, 4, "k2")
    eligible = _question(session, 4, 4, "k3")
    same = _question(session, 5, 2, "k3")
    easier = _question(session, 6, 1, "k3")
    calculation = _question(session, 7, 3, "k3", "计算题")
    return _paper(session, [target, reference]), target, reference, blocked, eligible, same, easier, calculation


def test_combined_constraints_change_actual_candidate_set(session):
    paper, target, _reference, blocked, eligible, _same, _easier, _calculation = _seed(session)
    result = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=ReplacementIntent(target_position=1, difficulty_direction="harder", avoid_similarity_with_question_numbers=[2]))
    assert result.ok
    assert result.recommended_question.question_id == eligible.id
    assert result.recommended_question.difficulty > 2
    assert blocked.id != result.recommended_question.question_id  # blocked shares reference knowledge k2
    assert result.candidate_stats["knowledge_constraints"] < result.candidate_stats["not_already_in_paper"]


def test_target_and_avoid_knowledge_and_exact_difficulty_are_hard_filters(session):
    paper, _target, _reference, blocked, eligible, same, easier, _calculation = _seed(session)
    exact = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=ReplacementIntent(target_position=1, target_difficulty=4, target_knowledge_node_ids=["k3"]))
    assert exact.ok and exact.recommended_question.question_id == eligible.id and exact.recommended_question.difficulty == 4
    avoided = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=ReplacementIntent(target_position=1, difficulty_direction="harder", avoid_knowledge_node_ids=["k2"]))
    assert avoided.ok and avoided.recommended_question.question_id == eligible.id
    same_result = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=ReplacementIntent(target_position=1, difficulty_direction="same"))
    assert same_result.ok and same_result.recommended_question.question_id == same.id
    easier_result = dry_run_replace_question(session, paper_id=paper.id, version_id=paper.id, intent=ReplacementIntent(target_position=1, difficulty_direction="easier"))
    assert easier_result.ok and easier_result.recommended_question.question_id == easier.id


class _Backend:
    def __init__(self, *responses): self.responses = list(responses)
    def complete(self, messages, tools): return self.responses.pop(0)


def _tool(name, arguments=None):
    return {"message": {"tool_calls": [{"id": "c2-call", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}]}}


def _final(text="完成。"):
    return {"message": {"content": text}}


def test_excluding_current_knowledge_requires_target_is_a_deterministic_guard(session):
    paper, target, *_ = _seed(session)
    blocked = ReplacementIntent(target_position=1, difficulty_direction="harder", avoid_knowledge_node_ids=["k1"])
    allowed = blocked.model_copy(update={"target_knowledge_node_ids": ["k3"]})
    assert replacement_requires_target_knowledge(session, version_id=paper.id, intent=blocked)
    assert not replacement_requires_target_knowledge(session, version_id=paper.id, intent=allowed)


def test_adjustment_plan_is_persisted_preview_and_stale_when_version_changes(session):
    paper, *_rest, calculation = _seed(session)
    before = session.scalar(select(func.count()).select_from(Paper))
    preview = preview_adjust_paper(session, paper_id=paper.id, question_type_changes={"选择题": -1, "计算题": 1})
    assert preview.ok and preview.plan.status == "pending"
    assert preview.plan.base_paper_version_id == paper.id
    assert preview.plan.before_summary.question_count == preview.plan.after_summary.question_count == 2
    assert preview.plan.before_summary.score_total == preview.plan.after_summary.score_total == 10
    assert preview.plan.after_summary.question_type_distribution["计算题"] == 1
    assert session.scalar(select(func.count()).select_from(Paper)) == before
    assert validate_adjustment_plan_freshness(session, plan_id=preview.plan.plan_id, current_version_id=paper.id).ok
    assert validate_adjustment_plan_freshness(session, plan_id=preview.plan.plan_id, current_version_id="new-version").blocking_errors == ["stale_adjustment_plan"]


def test_unbalanced_adjustment_plan_is_blocked(session):
    paper, *_ = _seed(session)
    preview = preview_adjust_paper(session, paper_id=paper.id, question_type_changes={"选择题": -1})
    assert not preview.ok
    assert preview.plan.status == "blocked"
    assert "question_count_change_not_balanced" in preview.blocking_errors


def test_remove_question_preserves_current_total_by_deterministic_rebalance(session):
    paper, *_ = _seed(session)
    preview = preview_adjust_paper(session, paper_id=paper.id, remove_positions=[2])
    assert preview.ok
    assert preview.requested_total_score == 10
    assert preview.plan.after_summary.question_count == 1
    assert preview.plan.after_summary.score_total == 10
    assert [operation.type for operation in preview.plan.operations] == ["remove_question", "change_score"]
    assert preview.plan.operations[1].score_before == 5
    assert preview.plan.operations[1].score_after == 10


def test_remove_question_and_change_total_apply_as_one_atomic_version(session):
    paper, target, *_ = _seed(session)
    preview = preview_adjust_paper(
        session, paper_id=paper.id, remove_positions=[2], target_total_score=8
    )
    assert preview.ok
    before = session.scalar(select(func.count()).select_from(Paper))
    applied = confirm_adjust_paper(
        session, plan_id=preview.plan.plan_id,
        paper_id=paper.id, current_version_id=paper.id,
    )
    assert applied.ok
    assert session.scalar(select(func.count()).select_from(Paper)) == before + 1
    version = session.get(Paper, applied.new_version_id)
    items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == version.id)))
    assert version.total_score == 8
    assert len(items) == 1
    assert items[0].question_id == target.id
    assert items[0].position == 1
    assert items[0].score == 8
    assert version.blueprint_id != paper.blueprint_id
    blueprint = session.get(PaperBlueprintRecord, version.blueprint_id).blueprint_json
    assert blueprint["total_questions"] == 1
    assert blueprint["total_score"] == 8
    assert blueprint["question_type_counts"] == {"选择题": 1}
    assert version.status == version.validation_status == "passed"


def test_invalid_removal_and_unbalance_need_no_partial_version(session):
    paper, *_ = _seed(session)
    missing = preview_adjust_paper(session, paper_id=paper.id, remove_positions=[99])
    assert not missing.ok and "remove_position_not_found" in missing.blocking_errors
    all_questions = preview_adjust_paper(session, paper_id=paper.id, remove_positions=[1, 2])
    assert not all_questions.ok and "cannot_remove_all_questions" in all_questions.blocking_errors


def test_confirm_adjustment_is_one_atomic_version_and_is_idempotent(session):
    paper, *_rest, calculation = _seed(session)
    second_calculation = _question(session, 8, 3, "k3", "计算题")
    preview = preview_adjust_paper(session, paper_id=paper.id, question_type_changes={"选择题": -2, "计算题": 2})
    assert preview.ok and len(preview.plan.operations) == 2
    before = session.scalar(select(func.count()).select_from(Paper))
    applied = confirm_adjust_paper(session, plan_id=preview.plan.plan_id, paper_id=paper.id, current_version_id=paper.id)
    assert applied.ok
    assert session.scalar(select(func.count()).select_from(Paper)) == before + 1
    version = session.get(Paper, applied.new_version_id)
    assert version.version == 2
    items = list(session.scalars(select(PaperItem).where(PaperItem.paper_id == version.id).order_by(PaperItem.position)))
    assert {item.question_id for item in items} >= {calculation.id, second_calculation.id}
    assert confirm_adjust_paper(session, plan_id=preview.plan.plan_id, paper_id=version.id, current_version_id=version.id).blocking_errors == ["adjustment_plan_already_applied"]
    assert session.scalar(select(func.count()).select_from(Paper)) == before + 1
    undone = undo_paper_operations(session, version.id)
    original_ids = [item.question_id for item in session.scalars(select(PaperItem).where(PaperItem.paper_id == paper.id).order_by(PaperItem.position))]
    assert [item.question_id for item in session.scalars(select(PaperItem).where(PaperItem.paper_id == undone.paper_id).order_by(PaperItem.position))] == original_ids


def test_confirm_stale_or_failed_plan_never_writes_partial_version(session):
    paper, *_rest, _calculation = _seed(session)
    preview = preview_adjust_paper(session, paper_id=paper.id, question_type_changes={"选择题": -1, "计算题": 1})
    before = session.scalar(select(func.count()).select_from(Paper))
    current, _clones = _clone_version(session, paper, list(session.scalars(select(PaperItem).where(PaperItem.paper_id == paper.id))))
    stale = confirm_adjust_paper(session, plan_id=preview.plan.plan_id, paper_id=current.id, current_version_id=current.id)
    assert stale.blocking_errors == ["stale_adjustment_plan"]
    assert session.scalar(select(func.count()).select_from(Paper)) == before + 1
    preview = preview_adjust_paper(session, paper_id=paper.id, question_type_changes={"选择题": -1, "计算题": 1})
    replacement_id = preview.plan.operations[0].new_question_id
    session.get(Question, replacement_id).is_active = False
    failed = confirm_adjust_paper(session, plan_id=preview.plan.plan_id, paper_id=paper.id, current_version_id=paper.id)
    assert failed.blocking_errors == ["replacement_question_unavailable"]
    assert session.scalar(select(func.count()).select_from(Paper)) == before + 1


def test_agent_pending_adjustment_requires_explicit_confirm(session):
    paper, *_ = _seed(session)
    no_pending = run_teacher_agent(session, "确认", conversation_id="adjust", paper_id=paper.id, version_id=paper.id, backend=_Backend(_tool("confirm_adjust_paper"), _final("没有待确认方案。")))
    assert no_pending.blocking_errors == ["no_pending_adjustment"]
    preview = run_teacher_agent(session, "选择题少一道，多一道计算题", conversation_id="adjust", paper_id=paper.id, version_id=paper.id, backend=_Backend(_tool("preview_adjust_paper", {"question_type_changes": {"选择题": -1, "计算题": 1}}), _final("方案待确认。")))
    assert preview.status == "waiting_confirmation" and preview.adjustment_preview.plan.status == "pending"
    confirmed = run_teacher_agent(session, "按这个方案改", conversation_id="adjust", paper_id=paper.id, version_id=paper.id, backend=_Backend(_tool("confirm_adjust_paper"), _final("已应用。")))
    assert confirmed.status == "completed" and confirmed.adjustment.new_version_id


def test_agent_adjustment_tool_accepts_removal_and_target_total(session):
    paper, *_ = _seed(session)
    preview = run_teacher_agent(
        session, "删除第二题，总分改成8分", conversation_id="adjust-remove",
        paper_id=paper.id, version_id=paper.id,
        backend=_Backend(
            _tool("preview_adjust_paper", {"remove_positions": [2], "target_total_score": 8}),
            _final("方案待确认。"),
        ),
    )
    assert preview.status == "waiting_confirmation"
    assert preview.adjustment_preview.plan.after_summary.question_count == 1
    assert preview.adjustment_preview.plan.after_summary.score_total == 8


def test_adjustment_input_accepts_total_score_compatibility_alias_and_rejects_unknown_fields():
    assert PreviewAdjustmentInput.model_validate({"total_score": 80}).target_total_score == 80
    with pytest.raises(Exception):
        PreviewAdjustmentInput.model_validate({"unexpected": True})


def test_pending_removal_plan_merges_later_total_score_patch(session):
    paper, *_ = _seed(session)
    first = run_teacher_agent(
        session, "删除第二题", conversation_id="adjust-patch",
        paper_id=paper.id, version_id=paper.id,
        backend=_Backend(
            _tool("preview_adjust_paper", {"remove_positions": [2]}),
            _final("删除第二题并保持原总分，等待确认。"),
        ),
    )
    assert first.adjustment_preview.plan.after_summary.score_total == 10
    revised = run_teacher_agent(
        session, "总分改成8分", conversation_id="adjust-patch",
        paper_id=paper.id, version_id=paper.id,
        backend=_Backend(
            _tool("read_current_paper"),
            _final("请确认改成8分。"),
            _tool("preview_adjust_paper", {"target_total_score": 8}),
            _final("删除第二题且总分8分的新方案等待确认。"),
        ),
    )
    assert revised.status == "waiting_confirmation"
    assert revised.adjustment_preview.requested_remove_positions == [2]
    assert revised.adjustment_preview.plan.after_summary.question_count == 1
    assert revised.adjustment_preview.plan.after_summary.score_total == 8
    confirmed = run_teacher_agent(
        session, "是的", conversation_id="adjust-patch",
        paper_id=paper.id, version_id=paper.id,
        backend=_Backend(
            _tool("confirm_adjust_paper"),
            _final("已应用。"),
        ),
    )
    version = session.get(Paper, confirmed.adjustment.new_version_id)
    assert version.total_score == 8
    assert version.validation_status == "passed"


def test_pending_adjustment_cannot_be_verbally_updated_without_new_preview(session):
    paper, *_ = _seed(session)
    run_teacher_agent(
        session, "删除第二题", conversation_id="adjust-guard",
        paper_id=paper.id, version_id=paper.id,
        backend=_Backend(
            _tool("preview_adjust_paper", {"remove_positions": [2]}),
            _final("等待确认。"),
        ),
    )
    result = run_teacher_agent(
        session, "总分改成8分", conversation_id="adjust-guard",
        paper_id=paper.id, version_id=paper.id,
        backend=_Backend(
            _tool("read_current_paper"),
            _final("请确认改成8分。"),
            _final("已经改好了，请确认。"),
        ),
    )
    assert result.status == "needs_clarification"
    assert "pending_adjustment_not_updated" in result.blocking_errors
    assert "不会确认旧方案" in result.message


def test_model_unavailable_does_not_activate_hardcoded_intent_routing(session):
    paper, *_ = _seed(session)
    result = run_teacher_agent(
        session, "选择题第一题换一道", conversation_id="no-model",
        paper_id=paper.id, version_id=paper.id,
    )
    assert result.status == "failed"
    assert result.blocking_errors == ["agent_model_unavailable"]
