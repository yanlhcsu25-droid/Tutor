from datetime import UTC, datetime

import pytest
from sqlalchemy import select

import calculus_agent.agent.tools.analysis_tools as analysis_tools
import calculus_agent.papers.workflow as workflow
from calculus_agent.agent.tool_registry import (
    AgentExecutionContext,
    PreviewAddQuestionInput,
    build_agent_tools,
    execute_tool,
)
from calculus_agent.agent.tools.add_tools import preview_add_question
from calculus_agent.agent.tools.analysis_tools import confirm_adjust_paper
from calculus_agent.agent.tools.version_tools import run_version_operation
from calculus_agent.agent.version_parser import VersionOperationIntent
from calculus_agent.models import (
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    PaperOperationHistory,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from calculus_agent.papers.addressing import resolve_section_item_from_items
from calculus_agent.schemas import ValidationReportRead


def _fake_validate_paper(session, paper_id: str) -> ValidationReportRead:
    paper = session.get(Paper, paper_id)
    paper.status = "passed"
    paper.validation_status = "passed"
    session.flush()
    return ValidationReportRead(
        id=f"fake-{paper_id}",
        paper_id=paper_id,
        passed=True,
        violations=[],
        created_at=datetime.now(UTC),
    )


def _question(session, *, sid: str, text: str, qtype: str, node_id: str) -> Question:
    draft = QuestionDraft(
        source_name="test",
        source_item_id=sid,
        variant=1,
        subject="高等数学",
        question_type=qtype,
        question_text=text,
        normalized_fingerprint=f"fp-{sid}",
        status="approved",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        question_text=text,
        question_type=qtype,
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
        knowledge_match_status="current",
    )
    session.add(question)
    session.flush()

    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=node_id,
            relation_type="primary",
        )
    )
    session.add(
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=3,
            estimated_time_min=5,
            reasoning_depth=2,
            calculation_load=2,
            knowledge_depth=2,
            comprehensive_level=2,
            confidence=1.0,
            profile_source="test",
            profile_status="approved",
            reason="test",
        )
    )
    session.flush()
    return question


def _make_paper(session, *, suffix=""):
    node = KnowledgeNode(
        node_type="section",
        name=f"phase3-node{suffix}",
        normalized_name=f"phase3-node{suffix}",
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(node)
    session.flush()

    blueprint = PaperBlueprintRecord(
        title=f"phase3{suffix}",
        blueprint_json={
            "title": f"phase3{suffix}",
            "total_questions": 6,
            "total_score": 40,
            "question_type_counts": {"选择题": 2, "填空题": 2, "计算题": 2},
            "_agent_metadata": {"scope_node_ids": [node.id]},
        },
        status="used",
    )
    session.add(blueprint)
    session.flush()

    paper = Paper(
        blueprint_id=blueprint.id,
        version=1,
        status="passed",
        title=f"phase3{suffix}",
        total_score=40,
        validation_status="passed",
    )
    session.add(paper)
    session.flush()
    paper.root_paper_id = paper.id
    session.flush()

    specs = [
        (f"a{suffix}", "A", "选择题", 5),
        (f"b{suffix}", "B", "选择题", 5),
        (f"c{suffix}", "C", "填空题", 5),
        (f"d{suffix}", "D", "填空题", 5),
        (f"e{suffix}", "E", "计算题", 10),
        (f"f{suffix}", "F", "计算题", 10),
    ]
    questions = {}
    for position, (sid, text, qtype, score) in enumerate(specs, 1):
        q = _question(session, sid=sid, text=text, qtype=qtype, node_id=node.id)
        questions[sid] = q
        session.add(
            PaperItem(
                paper_id=paper.id,
                question_id=q.id,
                section=qtype,
                position=position,
                score=score,
                locked=False,
            )
        )
    session.flush()

    x = _question(
        session,
        sid=f"x{suffix}",
        text="X",
        qtype="填空题",
        node_id=node.id,
    )
    return paper, node, questions, x


def _items(session, paper_id):
    return list(
        session.scalars(
            select(PaperItem)
            .where(PaperItem.paper_id == paper_id)
            .order_by(PaperItem.position)
        )
    )


def _texts(session, paper_id):
    return [
        session.get(Question, item.question_id).question_text
        for item in _items(session, paper_id)
    ]


def test_preview_add_is_pure_and_inserts_at_section_end(session):
    paper, _node, _questions, x = _make_paper(session)
    before = [(i.id, i.position, i.question_id, i.score) for i in _items(session, paper.id)]

    preview = preview_add_question(
        session,
        paper_id=paper.id,
        question_type="填空题",
    )

    assert preview.ok is True
    assert preview.score == 5
    assert preview.selected_question_id == x.id
    assert preview.insert_position == 5
    assert preview.plan.before_summary.question_count == 6
    assert preview.plan.after_summary.question_count == 7
    assert preview.plan.before_summary.score_total == 40
    assert preview.plan.after_summary.score_total == 45
    assert preview.plan.after_summary.question_type_distribution == {
        "选择题": 2,
        "填空题": 3,
        "计算题": 2,
    }

    after = [(i.id, i.position, i.question_id, i.score) for i in _items(session, paper.id)]
    assert after == before


def test_preview_add_requires_or_disambiguates_score(session):
    paper, node, _questions, _x = _make_paper(session)
    _question(session, sid="proof", text="P", qtype="证明题", node_id=node.id)

    missing = preview_add_question(
        session,
        paper_id=paper.id,
        question_type="证明题",
    )
    assert missing.ok is False
    assert missing.blocking_errors == ["add_question_score_required"]
    assert missing.clarification_questions

    fill = [item for item in _items(session, paper.id) if item.section == "填空题"]
    fill[1].score = 10
    session.flush()

    ambiguous = preview_add_question(
        session,
        paper_id=paper.id,
        question_type="填空题",
    )
    assert ambiguous.ok is False
    assert ambiguous.blocking_errors == ["add_question_score_ambiguous"]


def test_confirm_add_creates_child_and_preserves_source(session, monkeypatch):
    monkeypatch.setattr(analysis_tools, "validate_paper", _fake_validate_paper)
    paper, _node, _questions, x = _make_paper(session)

    preview = preview_add_question(session, paper_id=paper.id, question_type="填空题")
    assert preview.ok and preview.plan

    confirmed = confirm_adjust_paper(
        session,
        plan_id=preview.plan.plan_id,
        paper_id=paper.id,
        current_version_id=paper.id,
    )
    assert confirmed.ok is True
    child = session.get(Paper, confirmed.new_version_id)

    assert child.version == 2
    assert child.parent_version_id == paper.id
    assert child.root_paper_id == paper.id
    assert child.total_score == 45

    assert _texts(session, paper.id) == ["A", "B", "C", "D", "E", "F"]
    assert _texts(session, child.id) == ["A", "B", "C", "D", "X", "E", "F"]
    assert [item.position for item in _items(session, child.id)] == list(range(1, 8))

    fill3 = resolve_section_item_from_items(
        _items(session, child.id),
        section_type="填空题",
        section_order=3,
    )
    assert fill3 and fill3.question_id == x.id

    history = session.scalar(
        select(PaperOperationHistory).where(
            PaperOperationHistory.result_paper_id == child.id
        )
    )
    assert history is not None
    assert any(op["type"] == "add_question" for op in history.operations_json)


def test_add_plan_stale_and_double_confirm_are_rejected(session, monkeypatch):
    monkeypatch.setattr(analysis_tools, "validate_paper", _fake_validate_paper)

    paper, _node, _questions, _x = _make_paper(session)
    preview = preview_add_question(session, paper_id=paper.id, question_type="填空题")
    assert preview.ok and preview.plan

    other = Paper(
        blueprint_id=paper.blueprint_id,
        root_paper_id=paper.id,
        parent_version_id=paper.id,
        version=2,
        status="passed",
        title="other",
        total_score=40,
        validation_status="passed",
    )
    session.add(other)
    session.flush()

    stale = confirm_adjust_paper(
        session,
        plan_id=preview.plan.plan_id,
        paper_id=other.id,
        current_version_id=other.id,
    )
    assert stale.ok is False
    assert stale.blocking_errors == ["stale_adjustment_plan"]

    p2, _n2, _q2, _x2 = _make_paper(session, suffix="-2")
    fresh = preview_add_question(session, paper_id=p2.id, question_type="填空题")
    assert fresh.ok and fresh.plan

    first = confirm_adjust_paper(
        session,
        plan_id=fresh.plan.plan_id,
        paper_id=p2.id,
        current_version_id=p2.id,
    )
    assert first.ok is True

    second = confirm_adjust_paper(
        session,
        plan_id=fresh.plan.plan_id,
        paper_id=p2.id,
        current_version_id=p2.id,
    )
    assert second.ok is False
    assert second.blocking_errors == ["adjustment_plan_already_applied"]


def test_add_undo_redo_restore_preserve_section_addresses(session, monkeypatch):
    monkeypatch.setattr(analysis_tools, "validate_paper", _fake_validate_paper)
    monkeypatch.setattr(workflow, "validate_paper", _fake_validate_paper)

    paper, _node, _questions, x = _make_paper(session)
    preview = preview_add_question(session, paper_id=paper.id, question_type="填空题")
    confirmed = confirm_adjust_paper(
        session,
        plan_id=preview.plan.plan_id,
        paper_id=paper.id,
        current_version_id=paper.id,
    )
    assert confirmed.ok
    v2 = confirmed.new_version_id

    undo = run_version_operation(
        session,
        paper_id=v2,
        version_id=v2,
        intent=VersionOperationIntent(action="undo"),
    )
    assert undo.ok
    assert _texts(session, undo.current_version_id) == ["A", "B", "C", "D", "E", "F"]

    redo = run_version_operation(
        session,
        paper_id=undo.current_version_id,
        version_id=undo.current_version_id,
        intent=VersionOperationIntent(action="redo"),
    )
    assert redo.ok
    assert _texts(session, redo.current_version_id) == ["A", "B", "C", "D", "X", "E", "F"]

    fill3 = resolve_section_item_from_items(
        _items(session, redo.current_version_id),
        section_type="填空题",
        section_order=3,
    )
    assert fill3 and fill3.question_id == x.id

    restore = run_version_operation(
        session,
        paper_id=redo.current_version_id,
        version_id=redo.current_version_id,
        intent=VersionOperationIntent(action="restore", target_version=1),
    )
    assert restore.ok
    assert _texts(session, restore.current_version_id) == ["A", "B", "C", "D", "E", "F"]


def test_add_tool_contract(session):
    paper, _node, _questions, _x = _make_paper(session)
    request = PreviewAddQuestionInput(question_type="填空题")
    assert request.score is None

    context = AgentExecutionContext(
        session=session,
        conversation_id=None,
        paper_id=paper.id,
        version_id=paper.id,
        state_store=None,
    )
    tools = build_agent_tools(context)
    assert "preview_add_question" in tools

    result = execute_tool(
        tools["preview_add_question"],
        {"question_type": "填空题"},
    )
    assert result.payload["question_type"] == "填空题"


def test_add_tool_rejects_unknown_fields():
    with pytest.raises(Exception):
        PreviewAddQuestionInput(
            question_type="填空题",
            score=5,
            position=99,
        )
