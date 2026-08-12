from sqlalchemy import select

from calculus_agent.knowledge.classification import (
    confirm_question_knowledge,
    ensure_calculus_taxonomy,
    suggest_question_knowledge,
)
from calculus_agent.models import Question, QuestionDraft, QuestionKnowledgeLink


def _published_question(session) -> Question:
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id="ocr-1",
        variant=1,
        subject="高等数学",
        grade="大学",
        question_type="calculation",
        question_text=r"求极限 $\lim_{x\to0}\frac{\sin x}{x}$",
        reference_answers_json=["1"],
        normalized_fingerprint="k" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade=draft.grade,
        question_type=draft.question_type,
        final_answer="1",
        solution_json={"solution_steps": ["使用重要极限"]},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    return question


def test_suggests_only_controlled_calculus_nodes(session):
    question = _published_question(session)
    suggestions = suggest_question_knowledge(session, question)
    assert suggestions
    assert suggestions[0]["name"] in {"函数极限", "两个重要极限"}
    assert all(item["knowledge_node_id"] for item in suggestions)


def test_confirm_replaces_question_knowledge_links(session):
    question = _published_question(session)
    nodes = ensure_calculus_taxonomy(session)
    selected = [nodes[0].id, nodes[4].id]
    confirm_question_knowledge(session, question.id, selected)
    links = list(session.scalars(select(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question.id
    )).all())
    assert [link.knowledge_node_id for link in links] == selected
    assert [link.relation_type for link in links] == ["related", "related"]
