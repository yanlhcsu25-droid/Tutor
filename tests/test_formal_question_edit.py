from sqlalchemy import select

from calculus_agent.api import (
    FormalQuestionUpdateRequest,
    retire_formal_question,
    search_questions,
    update_formal_question,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    OcrImportDraft,
    OcrImportSource,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)


def _seed(session):
    book = Textbook(name="高等数学", edition="测试版", is_active=True)
    session.add(book)
    session.flush()
    chapter = CurriculumNode(
        textbook_id=book.id,
        node_type="chapter",
        title="函数与极限",
        sort_order=10,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    nodes = []
    for order, name in enumerate(("函数的极限", "无穷小的比较"), start=11):
        curriculum = CurriculumNode(
            textbook_id=book.id,
            parent_id=chapter.id,
            node_type="section",
            title=name,
            sort_order=order,
            review_status="approved",
        )
        session.add(curriculum)
        session.flush()
        node = KnowledgeNode(
            curriculum_node_id=curriculum.id,
            node_type="concept",
            name=name,
            normalized_name=name,
            source_type="textbook_directory",
            review_status="approved",
        )
        session.add(node)
        session.flush()
        nodes.append(node)

    source = OcrImportSource(
        id="src_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        original_name="source.pdf",
        stored_path="/tmp/source.pdf",
        sha256="a" * 64,
        page_count=1,
        processing_status="done",
    )
    origin = OcrImportDraft(
        id="q_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        source_id=source.id,
        page_number=1,
        original_number="1",
        ocr_markdown="original",
        edited_markdown="edited",
        review_status="published",
    )
    session.add_all((source, origin))
    session.flush()
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id=origin.id,
        variant=1,
        subject="高等数学",
        question_type="calculation",
        question_text="原题干",
        solution_text="原解析",
        reference_answers_json=[],
        normalized_fingerprint="f" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        id="d63107c7-b6da-4c9e-b322-17a91b3776bc",
        draft_id=draft.id,
        question_text="原题干",
        question_type="calculation",
        final_answer="原答案",
        solution_json={"solution_steps": ["原解析"]},
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
        knowledge_match_status="current",
    )
    session.add(question)
    session.flush()
    session.add(QuestionKnowledgeLink(
        question_id=question.id,
        knowledge_node_id=nodes[0].id,
        relation_type="primary_concept",
        confidence=1.0,
        evidence_json=[{"source": "seed"}],
    ))
    session.flush()
    return question, draft, origin, nodes


def _request(node_ids, **changes):
    values = {
        "question_text": "原题干",
        "solution_content": "原解析",
        "final_answer": "原答案",
        "question_type": "calculation",
        "chapter": "函数与极限",
        "knowledge_node_ids": node_ids,
        "original_number": "1",
        "source_page": 1,
        "difficulty": 3,
    }
    values.update(changes)
    return FormalQuestionUpdateRequest(**values)


def test_content_edit_keeps_question_id_and_marks_knowledge_stale(session):
    question, draft, _, nodes = _seed(session)

    result = update_formal_question(
        question.id,
        _request([nodes[0].id], question_text="修正后的题干", solution_content="修正后的解析"),
        session,
    )

    assert result.id == question.id
    assert question.question_text == draft.question_text == "修正后的题干"
    assert draft.solution_text == "修正后的解析"
    assert question.review_status == "approved"
    assert question.knowledge_match_status == "stale"
    assert session.scalar(select(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question.id
    )) is not None


def test_changing_controlled_knowledge_replaces_links_and_clears_stale(session):
    question, _, _, nodes = _seed(session)

    update_formal_question(
        question.id,
        _request([nodes[1].id], question_text="修正后的题干"),
        session,
    )

    links = list(session.scalars(select(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question.id
    )).all())
    assert [(link.knowledge_node_id, link.relation_type) for link in links] == [
        (nodes[1].id, "related")
    ]
    assert question.knowledge_match_status == "current"


def test_soft_delete_preserves_question_id_and_links_but_hides_search_result(session):
    question, _, _, _ = _seed(session)
    link_count = len(list(session.scalars(select(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question.id
    )).all()))

    result = retire_formal_question(question.id, session)

    assert result == {"question_id": question.id, "is_active": False, "deleted": False}
    assert session.get(Question, question.id) is question
    assert question.is_active is False
    assert len(list(session.scalars(select(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question.id
    )).all())) == link_count
    assert search_questions(
        query=question.id,
        source_name=None,
        question_type=None,
        limit=20,
        session=session,
    ) == []
