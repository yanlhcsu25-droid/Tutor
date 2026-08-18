from datetime import UTC, datetime
from sqlalchemy import select

import calculus_agent.agent.tools.analysis_tools as analysis_tools
import calculus_agent.papers.workflow as workflow
from calculus_agent.agent.tool_registry import AgentExecutionContext, PreviewAddQuestionInput, build_agent_tools
from calculus_agent.agent.tools.add_tools import preview_add_question
from calculus_agent.agent.tools.analysis_tools import confirm_adjust_paper
from calculus_agent.agent.tools.version_tools import run_version_operation
from calculus_agent.agent.version_parser import VersionOperationIntent
from calculus_agent.models import (
    KnowledgeNode, Paper, PaperBlueprintRecord, PaperItem, PaperOperationHistory,
    Question, QuestionDraft, QuestionKnowledgeLink, QuestionProfile,
)
from calculus_agent.papers.addressing import resolve_section_item_from_items
from calculus_agent.schemas import ValidationReportRead


def _fake_validate_paper(session, paper_id: str) -> ValidationReportRead:
    paper = session.get(Paper, paper_id)
    paper.status = "passed"
    paper.validation_status = "passed"
    session.flush()
    return ValidationReportRead(
        id=f"fake-{paper_id}", paper_id=paper_id, passed=True,
        violations=[], created_at=datetime.now(UTC),
    )


def _question(session, sid, text, qtype, node_id):
    draft = QuestionDraft(
        source_name="test", source_item_id=sid, variant=1, subject="高等数学",
        question_type=qtype, question_text=text,
        normalized_fingerprint=f"fp-{sid}", status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id, question_text=text, question_type=qtype,
        verification_status="manual_verified", review_status="approved",
        is_active=True, knowledge_match_status="current",
    )
    session.add(question)
    session.flush()
    session.add(QuestionKnowledgeLink(
        question_id=question.id, knowledge_node_id=node_id, relation_type="primary",
    ))
    session.add(QuestionProfile(
        question_id=question.id, profile_version=1, difficulty=3,
        estimated_time_min=5, reasoning_depth=2, calculation_load=2,
        knowledge_depth=2, comprehensive_level=2, confidence=1.0,
        profile_source="test", profile_status="approved", reason="test",
    ))
    session.flush()
    return question


def _fixture(session, suffix=""):
    node = KnowledgeNode(
        node_type="section", name=f"phase3-v2{suffix}",
        normalized_name=f"phase3-v2{suffix}", source_type="directory",
        confidence=1.0, review_status="approved",
    )
    session.add(node)
    session.flush()
    blueprint = PaperBlueprintRecord(
        title=f"phase3-v2{suffix}",
        blueprint_json={
            "title": f"phase3-v2{suffix}", "total_questions": 6, "total_score": 40,
            "question_type_counts": {"选择题": 2, "填空题": 2, "计算题": 2},
            "_agent_metadata": {"scope_node_ids": [node.id]},
        },
        status="used",
    )
    session.add(blueprint)
    session.flush()
    paper = Paper(
        blueprint_id=blueprint.id, version=1, status="passed",
        title=f"phase3-v2{suffix}", total_score=40, validation_status="passed",
    )
    session.add(paper)
    session.flush()
    paper.root_paper_id = paper.id

    specs = [
        (f"a{suffix}", "A", "选择题", 5),
        (f"b{suffix}", "B", "选择题", 5),
        (f"c{suffix}", "C", "填空题", 5),
        (f"d{suffix}", "D", "填空题", 5),
        (f"e{suffix}", "E", "计算题", 10),
        (f"f{suffix}", "F", "计算题", 10),
    ]
    for position, (sid, text, qtype, score) in enumerate(specs, 1):
        q = _question(session, sid, text, qtype, node.id)
        session.add(PaperItem(
            paper_id=paper.id, question_id=q.id, section=qtype,
            position=position, score=score, locked=False,
        ))
    session.flush()
    x = _question(session, f"x{suffix}", "X", "填空题", node.id)
    return paper, node, x


def _items(session, paper_id):
    return list(session.scalars(
        select(PaperItem).where(PaperItem.paper_id == paper_id).order_by(PaperItem.position)
    ))


def _texts(session, paper_id):
    return [session.get(Question, item.question_id).question_text for item in _items(session, paper_id)]


def test_add_preview_is_pure_and_score_is_inferred(session):
    paper, _node, x = _fixture(session)
    before = [(i.id, i.position, i.question_id, i.score) for i in _items(session, paper.id)]
    preview = preview_add_question(session, paper_id=paper.id, question_type="填空题")
    assert preview.ok
    assert preview.score == 5
    assert preview.selected_question_id == x.id
    assert preview.insert_position == 5
    assert preview.plan.before_summary.question_count == 6
    assert preview.plan.after_summary.question_count == 7
    assert preview.plan.before_summary.score_total == 40
    assert preview.plan.after_summary.score_total == 45
    after = [(i.id, i.position, i.question_id, i.score) for i in _items(session, paper.id)]
    assert after == before


def test_confirm_add_creates_child_and_section_address(session, monkeypatch):
    monkeypatch.setattr(analysis_tools, "validate_paper", _fake_validate_paper)
    paper, _node, x = _fixture(session)
    preview = preview_add_question(session, paper_id=paper.id, question_type="填空题")
    confirmed = confirm_adjust_paper(
        session, plan_id=preview.plan.plan_id,
        paper_id=paper.id, current_version_id=paper.id,
    )
    assert confirmed.ok
    child = session.get(Paper, confirmed.new_version_id)
    assert child.version == 2
    assert child.parent_version_id == paper.id
    assert child.total_score == 45
    assert _texts(session, paper.id) == ["A", "B", "C", "D", "E", "F"]
    assert _texts(session, child.id) == ["A", "B", "C", "D", "X", "E", "F"]
    fill3 = resolve_section_item_from_items(
        _items(session, child.id), section_type="填空题", section_order=3,
    )
    assert fill3 is not None and fill3.question_id == x.id
    history = session.scalar(select(PaperOperationHistory).where(
        PaperOperationHistory.result_paper_id == child.id
    ))
    assert history is not None
    assert any(op["type"] == "add_question" for op in history.operations_json)


def test_add_undo(session, monkeypatch):
    monkeypatch.setattr(analysis_tools, "validate_paper", _fake_validate_paper)
    monkeypatch.setattr(workflow, "validate_paper", _fake_validate_paper)
    paper, _node, _x = _fixture(session)
    preview = preview_add_question(session, paper_id=paper.id, question_type="填空题")
    confirmed = confirm_adjust_paper(
        session, plan_id=preview.plan.plan_id,
        paper_id=paper.id, current_version_id=paper.id,
    )
    undo = run_version_operation(
        session, paper_id=confirmed.new_version_id,
        version_id=confirmed.new_version_id,
        intent=VersionOperationIntent(action="undo"),
    )
    assert undo.ok
    assert _texts(session, undo.current_version_id) == ["A", "B", "C", "D", "E", "F"]


def test_add_tool_exists_and_schema_is_strict(session):
    paper, _node, _x = _fixture(session)
    tools = build_agent_tools(AgentExecutionContext(
        session=session, conversation_id=None, paper_id=paper.id,
        version_id=paper.id, state_store=None,
    ))
    assert "preview_add_question" in tools
    schema = PreviewAddQuestionInput.model_json_schema()
    assert schema.get("additionalProperties") is False
    assert set(schema["properties"]) == {"question_type", "score"}
