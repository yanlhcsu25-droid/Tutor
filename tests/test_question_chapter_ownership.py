"""Regression: owning chapter is independent from knowledge points."""

from calculus_agent.agent.schemas import GenerationConstraints, PaperGenerationRequest
from calculus_agent.api import (
    FormalQuestionUpdateRequest,
    search_questions,
    update_formal_question,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)
from calculus_agent.papers.selector import compose_paper
from calculus_agent.questions.chapter_assignment import (
    derive_default_chapter_from_knowledge,
)
from calculus_agent.schemas import PaperBlueprint


def _seed_curriculum(session):
    book = Textbook(name="高等数学", edition="test", is_active=True)
    session.add(book)
    session.flush()

    chapter1 = CurriculumNode(
        textbook_id=book.id, node_type="chapter", code="一",
        title="函数与极限", sort_order=10, review_status="approved",
    )
    chapter3 = CurriculumNode(
        textbook_id=book.id, node_type="chapter", code="三",
        title="微分中值定理与导数的应用", sort_order=30,
        review_status="approved",
    )
    session.add_all([chapter1, chapter3])
    session.flush()

    section1 = CurriculumNode(
        textbook_id=book.id, parent_id=chapter1.id, node_type="section",
        code="1", title="函数的极限", sort_order=11,
        review_status="approved",
    )
    section3 = CurriculumNode(
        textbook_id=book.id, parent_id=chapter3.id, node_type="section",
        code="1", title="洛必达法则", sort_order=31,
        review_status="approved",
    )
    session.add_all([section1, section3])
    session.flush()

    kp1 = KnowledgeNode(
        curriculum_node_id=section1.id, node_type="concept",
        name="函数的极限", normalized_name="函数的极限",
        source_type="directory", review_status="approved",
    )
    kp3 = KnowledgeNode(
        curriculum_node_id=section3.id, node_type="concept",
        name="洛必达法则", normalized_name="洛必达法则",
        source_type="directory", review_status="approved",
    )
    session.add_all([kp1, kp3])
    session.flush()
    return chapter1, chapter3, kp1, kp3


def _question(session, *, qid, chapter, knowledge, text):
    draft = QuestionDraft(
        source_name="ocr_doc", source_item_id=qid, variant=1,
        subject="高等数学", question_type="计算题",
        source_topic=chapter.title, question_text=text,
        reference_answers_json=[],
        normalized_fingerprint=(qid.replace("-", "") + "0" * 64)[:64],
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        id=qid, draft_id=draft.id,
        curriculum_chapter_id=chapter.id,
        question_text=text, question_type="计算题",
        solution_json={}, verification_status="manual_verified",
        review_status="approved", is_active=True,
        knowledge_match_status="current",
    )
    session.add(question)
    session.flush()
    for node in knowledge:
        session.add(QuestionKnowledgeLink(
            question_id=question.id, knowledge_node_id=node.id,
            relation_type="related", confidence=1.0, evidence_json=[],
        ))
    session.flush()
    return question, draft


def test_chapter_1_generation_excludes_chapter_3_question_with_chapter_1_knowledge(session):
    chapter1, chapter3, kp1, kp3 = _seed_curriculum(session)
    q1, _ = _question(
        session, qid="11111111-1111-4111-8111-111111111111",
        chapter=chapter1, knowledge=[kp1], text="第一章题",
    )
    q3, _ = _question(
        session, qid="33333333-3333-4333-8333-333333333333",
        chapter=chapter3, knowledge=[kp1, kp3],
        text="第三章题，但需要函数极限",
    )

    request = PaperGenerationRequest(
        blueprint=PaperBlueprint(
            title="第一章测试卷", total_questions=1, total_score=10,
            question_type_counts={"计算题": 1},
        ),
        constraints=GenerationConstraints(
            scope=["第一章"],
            scope_chapter_ids=[chapter1.id],
            scope_node_ids=[kp1.id],
        ),
    )
    preview = compose_paper(session, request)

    assert preview.feasible
    assert [item.question_id for item in preview.items] == [q1.id]
    assert q3.id not in {item.question_id for item in preview.items}


def test_chapter_3_generation_can_use_cross_chapter_knowledge_question(session):
    chapter1, chapter3, kp1, kp3 = _seed_curriculum(session)
    q3, _ = _question(
        session, qid="33333333-3333-4333-8333-333333333333",
        chapter=chapter3, knowledge=[kp1, kp3],
        text="第三章题，但需要函数极限",
    )
    request = PaperGenerationRequest(
        blueprint=PaperBlueprint(
            title="第三章测试卷", total_questions=1, total_score=10,
            question_type_counts={"计算题": 1},
        ),
        constraints=GenerationConstraints(
            scope=["第三章"], scope_chapter_ids=[chapter3.id],
            scope_node_ids=[kp3.id],
        ),
    )
    preview = compose_paper(session, request)

    assert preview.feasible
    assert [item.question_id for item in preview.items] == [q3.id]


def test_default_cross_chapter_assignment_chooses_later_chapter(session):
    chapter1, chapter3, kp1, kp3 = _seed_curriculum(session)
    selected = derive_default_chapter_from_knowledge(
        session, [kp1.id, kp3.id]
    )
    assert selected is not None
    assert selected.id == chapter3.id


def test_manual_chapter_assignment_survives_knowledge_change(session):
    chapter1, chapter3, kp1, kp3 = _seed_curriculum(session)
    question, draft = _question(
        session, qid="33333333-3333-4333-8333-333333333333",
        chapter=chapter3, knowledge=[kp1, kp3], text="第三章题",
    )
    result = update_formal_question(
        question.id,
        FormalQuestionUpdateRequest(
            question_text="第三章题", solution_content="解答",
            final_answer=None, question_type="计算题",
            chapter_id=chapter3.id, chapter="第三章",
            knowledge_node_ids=[kp1.id], difficulty=3,
        ),
        session,
    )
    assert question.curriculum_chapter_id == chapter3.id
    assert result.chapter_id == chapter3.id
    assert result.chapter is not None and "第三章" in result.chapter


def test_question_bank_chapter_filter_uses_ownership(session):
    chapter1, chapter3, kp1, kp3 = _seed_curriculum(session)
    q1, _ = _question(
        session, qid="11111111-1111-4111-8111-111111111111",
        chapter=chapter1, knowledge=[kp1], text="第一章题",
    )
    q3, _ = _question(
        session, qid="33333333-3333-4333-8333-333333333333",
        chapter=chapter3, knowledge=[kp1, kp3],
        text="第三章题，但也有第一章知识点",
    )
    rows = search_questions(
        query="", question_type=None, source_name=None,
        publish_source=None, chapter_id=chapter1.id,
        limit=50, session=session,
    )
    ids = {row.id for row in rows}
    assert q1.id in ids
    assert q3.id not in ids
