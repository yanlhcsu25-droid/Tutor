from calculus_agent.api import search_questions
from calculus_agent.models import Question, QuestionDraft


def _add_question(session, *, question_id: str, text: str, source_name: str = "ocr_import"):
    draft = QuestionDraft(
        id=f"draft-{question_id}",
        source_name=source_name,
        source_item_id=question_id,
        variant=1,
        subject="高等数学",
        question_type="calculation",
        question_text=text,
        reference_answers_json=[],
        normalized_fingerprint=question_id.replace("-", "")[:32].ljust(64, "0"),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        id=question_id,
        draft_id=draft.id,
        question_text=text,
        question_type="calculation",
        solution_json={},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    return question


def _search(session, query: str):
    return search_questions(query=query, source_name=None, question_type=None, limit=20, session=session)


def test_question_search_matches_full_uuid(session):
    target = _add_question(
        session,
        question_id="86484c4b-76fe-45b8-904e-5ab1a0052b2d",
        text="题干中没有 UUID",
    )
    _add_question(
        session,
        question_id="11111111-1111-4111-a111-111111111111",
        text="另一道题",
    )

    results = _search(session, target.id)

    assert [item.id for item in results] == [target.id]


def test_question_search_matches_uuid_prefix_of_eight_to_twelve_characters(session):
    target = _add_question(
        session,
        question_id="86484c4b-76fe-45b8-904e-5ab1a0052b2d",
        text="极限题",
    )

    assert [item.id for item in _search(session, "86484c4b")] == [target.id]
    assert [item.id for item in _search(session, "86484c4b76fe")] == [target.id]
    assert [item.id for item in _search(session, "86484c4b-76f")] == [target.id]


def test_question_search_keeps_question_text_keyword_matching(session):
    target = _add_question(
        session,
        question_id="86484c4b-76fe-45b8-904e-5ab1a0052b2d",
        text="利用夹逼准则求极限",
    )

    assert [item.id for item in _search(session, "夹逼准则")] == [target.id]


def test_short_hex_text_is_still_treated_as_question_keyword(session):
    target = _add_question(
        session,
        question_id="22222222-2222-4222-a222-222222222222",
        text="调试标记 abc1234",
    )

    assert [item.id for item in _search(session, "abc1234")] == [target.id]
