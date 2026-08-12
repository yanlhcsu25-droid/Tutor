from calculus_agent.models import (
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
)
from calculus_agent.questions.profiling import (
    list_question_profiles,
    profile_approved_questions,
    update_question_profile,
)
from calculus_agent.schemas import QuestionProfileUpdate
from calculus_agent import api


def _question(session, *, question_type="calculation", with_solution=True):
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id="profile-1",
        variant=1,
        subject="高等数学",
        question_type=question_type,
        question_text=r"计算 $\lim_{x\to0}\frac{\sin 2x}{x}$。",
        reference_answers_json=["2"],
        solution_text="利用重要极限计算。" if with_solution else None,
        normalized_fingerprint="a" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type=question_type,
        final_answer="2",
        solution_json={"solution_steps": ["先提出常数2。", "再使用第一重要极限。"]}
        if with_solution else {},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    node = KnowledgeNode(
        name="两个重要极限", normalized_name="两个重要极限",
        node_type="concept", source_type="trusted_dataset", review_status="approved",
    )
    session.add(node)
    session.flush()
    session.add(QuestionKnowledgeLink(
        question_id=question.id, knowledge_node_id=node.id,
        relation_type="primary_concept", confidence=1,
    ))
    session.flush()
    return question


def test_batch_profiling_is_valid_and_idempotent(session):
    question = _question(session)

    first = profile_approved_questions(session)
    second = profile_approved_questions(session)
    profiles = list_question_profiles(session)

    assert first.eligible == first.created == 1
    assert second.created == 0
    assert second.reused == 1
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.question_id == question.id
    assert 1 <= profile.difficulty <= 5
    assert 1 <= profile.reasoning_depth <= 5
    assert 1 <= profile.calculation_load <= 5
    assert profile.estimated_time_min > 0
    assert profile.profile_status == "pending"
    assert profile.calculation_load <= 2


def test_human_review_creates_approved_profile_version(session):
    _question(session)
    profile_approved_questions(session)
    pending = list_question_profiles(session)[0]

    approved = update_question_profile(
        session,
        pending.profile_id,
        QuestionProfileUpdate(
            difficulty=4,
            calculation_load=2,
            reason="需要选择重要极限并完成两步变形。",
            approve=True,
        ),
    )

    assert approved.profile_version == 2
    assert approved.profile_source == "corrected"
    assert approved.profile_status == "approved"
    assert approved.difficulty == 4
    assert approved.calculation_load == 2


def test_missing_solution_is_sent_to_review(session):
    _question(session, with_solution=False)

    result = profile_approved_questions(session)
    profile = list_question_profiles(session)[0]

    assert result.needs_review == 1
    assert profile.profile_status == "needs_review"
    assert "缺少解析" in profile.reason


def test_question_search_uses_only_approved_profile_filters(session):
    question = _question(session)
    profile_approved_questions(session)
    pending = list_question_profiles(session)[0]
    before = api.search_questions(
        query="", question_type=None, source_name="ocr_import", limit=20,
        difficulty_min=4, difficulty_max=None, calculation_load_max=2,
        comprehensive_level_min=None, estimated_time_max=None, session=session,
    )
    assert before == []

    update_question_profile(
        session, pending.profile_id,
        QuestionProfileUpdate(
            difficulty=4, calculation_load=2,
            reason="方法选择要求较高但计算过程较短。", approve=True,
        ),
    )
    after = api.search_questions(
        query="", question_type=None, source_name="ocr_import", limit=20,
        difficulty_min=4, difficulty_max=None, calculation_load_max=2,
        comprehensive_level_min=None, estimated_time_max=None, session=session,
    )
    assert [item.id for item in after] == [question.id]
    assert after[0].difficulty == 4
    assert after[0].calculation_load == 2


def test_multi_question_granularity_is_flagged_for_review(session):
    question = _question(session, question_type="selection")
    question.question_text = "以下两题中各选一个正确结论：(1) 极限A；(2) 极限B。"
    session.flush()

    result = profile_approved_questions(session)
    profile = list_question_profiles(session)[0]

    assert result.needs_review == 1
    assert profile.profile_status == "needs_review"
    assert profile.confidence < 0.8
    assert "粒度异常" in profile.reason
