"""定向测试：已审核题目允许教师手动修改题型（确定性 CRUD，不动知识点/审核态）。"""

from sqlalchemy import select

from calculus_agent.api import patch_question_type_value, search_questions
from calculus_agent.models import Question, QuestionDraft, QuestionKnowledgeLink


def _make_question(session, qid: str) -> Question:
    draft = QuestionDraft(
        id=f"draft-{qid}",
        source_name="ocr_import",
        source_item_id=qid,
        variant=1,
        subject="高等数学",
        question_type="unknown",
        question_text=f"题干 {qid}",
        reference_answers_json=[],
        normalized_fingerprint=qid.replace("-", "")[:32].ljust(64, "0"),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        id=qid,
        draft_id=draft.id,
        question_text=f"题干 {qid}",
        question_type="unknown",
        solution_json={},
        verification_status="manual_verified",
        review_status="approved",
    )
    session.add(question)
    session.add(QuestionKnowledgeLink(
        question_id=qid,
        knowledge_node_id="kn-1",
        relation_type="related",
    ))
    session.flush()
    return question


def test_patch_unknown_to_choice_keeps_review_status(session):
    q = _make_question(session, "q-patch-1")
    patch_question_type_value(session, q.id, "选择题")
    got = session.get(Question, q.id)
    assert got.question_type == "选择题"
    assert got.review_status == "approved"
    assert got.verification_status == "manual_verified"
    assert got.updated_at is not None
    draft = session.get(QuestionDraft, got.draft_id)
    assert draft.question_type == "选择题"
    links = session.scalars(
        select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.question_id == q.id)
    ).all()
    assert len(links) == 1 and links[0].knowledge_node_id == "kn-1"


def test_patch_does_not_trigger_knowledge_revalidation(session):
    q = _make_question(session, "q-patch-2")
    patch_question_type_value(session, q.id, "计算题")
    links = session.scalars(
        select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.question_id == q.id)
    ).all()
    assert len(links) == 1
    assert session.get(Question, q.id).knowledge_match_status == "current"


def test_patch_invalid_type_rejected(session):
    q = _make_question(session, "q-patch-3")
    try:
        patch_question_type_value(session, q.id, "诺米拉不存在的题型")
        assert False, "非法题型应被拒绝"
    except ValueError:
        pass
    # 原题型保持不变
    assert session.get(Question, q.id).question_type == "unknown"


def test_patch_missing_question_rejected(session):
    try:
        patch_question_type_value(session, "does-not-exist", "选择题")
        assert False, "不存在的题目应被拒绝"
    except ValueError:
        pass


def test_patch_reflects_in_candidate_query(session):
    q = _make_question(session, "q-patch-4")
    patch_question_type_value(session, q.id, "选择题")
    results = search_questions(query="", question_type="选择题", limit=20, session=session)
    assert q.id in [item.id for item in results]
