"""Question chapter derivation + materialized ownership regression.

Knowledge links + taxonomy deterministically derive the owner chapter; that
result is materialized on Question.curriculum_chapter_id and synchronized
whenever knowledge links are written.
"""
import uuid

from sqlalchemy import select

from calculus_agent.api import (
    FormalQuestionUpdateRequest,
    get_question_detail,
    search_questions,
    update_formal_question,
)
from calculus_agent.knowledge.chapter import (
    resolve_question_chapter,
    resolve_questions_chapters,
)
from calculus_agent.questions.chapter_assignment import (
    sync_question_chapter_ownership,
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


def _seed_taxonomy(session):
    book = Textbook(name="高等数学", edition="测试版", is_active=True)
    session.add(book)
    session.flush()
    chapters = {}
    ch_defs = [
        ("ch1", "函数与极限", 10),
        ("ch2", "导数与微分", 20),
        ("ch3", "微分中值定理与导数的应用", 30),
        ("ch5", "定积分", 50),
    ]
    kns = {}
    for key, title, order in ch_defs:
        ch = CurriculumNode(
            textbook_id=book.id, node_type="chapter", title=title,
            sort_order=order, review_status="approved",
        )
        session.add(ch)
        session.flush()
        chapters[key] = ch
        sec = CurriculumNode(
            textbook_id=book.id, parent_id=ch.id, node_type="section",
            title=f"{title}-节", sort_order=order + 1, review_status="approved",
        )
        session.add(sec)
        session.flush()
        kn = KnowledgeNode(
            curriculum_node_id=sec.id, node_type="concept", name=f"{title}-KP",
            normalized_name=f"{title}-KP", source_type="textbook_directory",
            review_status="approved",
        )
        session.add(kn)
        session.flush()
        kns[key] = kn

    # a second KP under chapter 3 (for the "two KPs in same chapter" case)
    sec3b = CurriculumNode(
        textbook_id=book.id, parent_id=chapters["ch3"].id, node_type="section",
        title="ch3-节2", sort_order=32, review_status="approved",
    )
    session.add(sec3b)
    session.flush()
    kn3b = KnowledgeNode(
        curriculum_node_id=sec3b.id, node_type="concept", name="ch3-KP2",
        normalized_name="ch3-KP2", source_type="textbook_directory",
        review_status="approved",
    )
    session.add(kn3b)
    session.flush()
    kns["ch3b"] = kn3b

    # a second textbook + chapter + KP (for the cross-textbook -> unresolvable case)
    book2 = Textbook(name="线性代数", edition="测试版", is_active=True)
    session.add(book2)
    session.flush()
    ch_other = CurriculumNode(
        textbook_id=book2.id, node_type="chapter", title="行列式",
        sort_order=10, review_status="approved",
    )
    session.add(ch_other)
    session.flush()
    sec_other = CurriculumNode(
        textbook_id=book2.id, parent_id=ch_other.id, node_type="section",
        title="行列式-节", sort_order=11, review_status="approved",
    )
    session.add(sec_other)
    session.flush()
    kn_other = KnowledgeNode(
        curriculum_node_id=sec_other.id, node_type="concept", name="行列式-KP",
        normalized_name="行列式-KP", source_type="textbook_directory",
        review_status="approved",
    )
    session.add(kn_other)
    session.flush()
    kns["other"] = kn_other

    return book, chapters, kns


def _make_question(session, kn_ids, source_topic=None, with_origin=False):
    if with_origin:
        src = OcrImportSource(
            id=str(uuid.uuid4()), original_name="s.pdf", stored_path="/tmp/s.pdf",
            sha256="a" * 64, page_count=1, processing_status="done",
        )
        session.add(src)
        session.flush()
        origin = OcrImportDraft(
            id=str(uuid.uuid4()), source_id=src.id, page_number=1,
            original_number="1", ocr_markdown="o", edited_markdown="e",
            review_status="published",
        )
        session.add(origin)
        session.flush()
        source_item_id = origin.id
    else:
        source_item_id = str(uuid.uuid4())

    draft = QuestionDraft(
        source_name="ocr_import" if with_origin else "manual",
        source_item_id=source_item_id,
        variant=1, subject="高等数学", question_type="calculation",
        question_text="q", solution_text="s",
        normalized_fingerprint="f" * 64, status="approved",
        source_topic=source_topic,
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id, question_text="q", question_type="calculation",
        solution_json={"solution_steps": []},
        verification_status="manual_verified", review_status="approved",
        is_active=True,
        knowledge_match_status="current" if kn_ids else "unmatched",
    )
    session.add(question)
    session.flush()
    for kn_id in kn_ids:
        session.add(QuestionKnowledgeLink(
            question_id=question.id, knowledge_node_id=kn_id,
            relation_type="related", confidence=1.0,
            evidence_json=[{"source": "test"}],
        ))
    session.flush()
    sync_question_chapter_ownership(session, question.id)
    return question, draft


def _update_request(kn_ids):
    return FormalQuestionUpdateRequest(
        question_text="q",
        solution_content="s",
        final_answer="a",
        question_type="calculation",
        chapter="ignored-by-server",
        knowledge_node_ids=kn_ids,
        difficulty=3,
        original_number="1",
        source_page=1,
    )


# ---- CASE 1: all KPs in chapter 3 -> chapter 3 ----------------------------
def test_all_kps_in_chapter_three(session):
    _, _, kns = _seed_taxonomy(session)
    q, _ = _make_question(session, [kns["ch3"].id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "ok"
    assert res.chapter_name == "微分中值定理与导数的应用"
    assert res.chapter_id == session.get(
        CurriculumNode, session.get(KnowledgeNode, kns["ch3"].id).curriculum_node_id
    ).parent_id


# ---- CASE 2: two different KPs both in chapter 3 -> chapter 3 -------------
def test_two_kps_same_chapter(session):
    _, _, kns = _seed_taxonomy(session)
    q, _ = _make_question(session, [kns["ch3"].id, kns["ch3b"].id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "ok"
    assert res.chapter_name == "微分中值定理与导数的应用"


# ---- CASE 3: chapter 1 + chapter 3 -> chapter 3 (NOT conflict) -----------
def test_cross_chapter_picks_latest(session):
    _, _, kns = _seed_taxonomy(session)
    q, _ = _make_question(session, [kns["ch1"].id, kns["ch3"].id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "ok"
    assert res.chapter_name == "微分中值定理与导数的应用"


# ---- CASE 4: chapter 1 + 2 + 5 -> chapter 5 ------------------------------
def test_three_chapters_picks_latest(session):
    _, _, kns = _seed_taxonomy(session)
    q, _ = _make_question(session, [kns["ch1"].id, kns["ch2"].id, kns["ch5"].id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "ok"
    assert res.chapter_name == "定积分"


# ---- CASE 5: change KPs ch3 -> ch1, next API read returns ch1 ------------
def test_change_kps_derives_new_chapter_without_chapter_update(session):
    _, _, kns = _seed_taxonomy(session)
    q, _ = _make_question(session, [kns["ch3"].id, kns["ch3b"].id], with_origin=True)

    before = get_question_detail(q.id, session)
    assert before.chapter == "微分中值定理与导数的应用"
    assert before.chapter_status == "ok"

    # Only knowledge links are rewritten; no chapter field is touched.
    after = update_formal_question(q.id, _update_request([kns["ch1"].id]), session)
    assert after.chapter == "函数与极限"
    assert after.chapter_status == "ok"
    # draft.source_topic must NOT have been silently set to a chapter.
    assert session.get(QuestionDraft, q.draft_id).source_topic is None


# ---- CASE 6: dirty source_topic ignored; API uses KP taxonomy -----------
def test_dirty_source_topic_does_not_control_chapter(session):
    _, _, kns = _seed_taxonomy(session)
    # source_topic holds a STALE chapter title, but KPs actually belong to ch3.
    q, _ = _make_question(
        session, [kns["ch3"].id, kns["ch3b"].id], source_topic="函数与极限"
    )
    detail = get_question_detail(q.id, session)
    assert detail.chapter == "微分中值定理与导数的应用"
    assert detail.chapter_status == "ok"
    assert detail.chapter != "函数与极限"


# ---- CASE 7: no KPs -> missing ------------------------------------------
def test_no_knowledge_points_is_missing(session):
    _seed_taxonomy(session)
    q, _ = _make_question(session, [])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "missing"
    assert res.chapter_id is None
    assert res.chapter_name is None

    detail = get_question_detail(q.id, session)
    assert detail.chapter_status == "missing"
    assert detail.chapter is None


# ---- CASE 8: taxonomy cannot resolve to chapter -> unresolvable ---------
def test_unresolvable_when_kp_cannot_trace_to_chapter(session):
    book, _, kns = _seed_taxonomy(session)
    # an orphan section with no chapter ancestor
    orphan_sec = CurriculumNode(
        textbook_id=book.id, parent_id=None, node_type="section",
        title="悬空节", sort_order=999, review_status="approved",
    )
    session.add(orphan_sec)
    session.flush()
    orphan_kn = KnowledgeNode(
        curriculum_node_id=orphan_sec.id, node_type="concept",
        name="悬空KP", normalized_name="悬空KP",
        source_type="textbook_directory", review_status="approved",
    )
    session.add(orphan_kn)
    session.flush()
    q, _ = _make_question(session, [orphan_kn.id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "unresolvable"
    assert res.chapter_id is None


def test_unresolvable_when_kp_has_no_curriculum_node(session):
    _seed_taxonomy(session)
    kn = KnowledgeNode(
        curriculum_node_id=None, node_type="concept", name="无节点KP",
        normalized_name="无节点KP", source_type="textbook_directory",
        review_status="approved",
    )
    session.add(kn)
    session.flush()
    q, _ = _make_question(session, [kn.id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "unresolvable"


def test_unresolvable_across_incomparable_textbooks(session):
    _, _, kns = _seed_taxonomy(session)
    q, _ = _make_question(session, [kns["ch3"].id, kns["other"].id])
    res = resolve_question_chapter(session, q.id)
    assert res.status == "unresolvable"


# ---- Batch resolver avoids N+1 -------------------------------------------
def test_batch_resolver_matches_individual(session):
    _, _, kns = _seed_taxonomy(session)
    q1, _ = _make_question(session, [kns["ch1"].id])
    q2, _ = _make_question(session, [kns["ch2"].id, kns["ch5"].id])
    q3, _ = _make_question(session, [])

    batch = resolve_questions_chapters(session, [q1.id, q2.id, q3.id])
    assert batch[q1.id].chapter_name == "函数与极限"
    assert batch[q2.id].chapter_name == "定积分"
    assert batch[q3.id].status == "missing"

    # batch results must equal the per-question resolver
    assert batch[q1.id] == resolve_question_chapter(session, q1.id)
    assert batch[q2.id] == resolve_question_chapter(session, q2.id)
    assert batch[q3.id] == resolve_question_chapter(session, q3.id)


# ---- CASE 9: valid ch1 KP + orphan KP -> unresolvable, absent from ch1 filter ----
def test_chapter_one_plus_orphan_kp_unresolvable_and_absent_from_filter(session):
    _, chapters, kns = _seed_taxonomy(session)
    # 失效知识点：curriculum_node_id 指向一个已不存在的目录节点。
    orphan_kn = KnowledgeNode(
        curriculum_node_id="deadbeef-0000-0000-0000-000000000000",
        node_type="concept", name="失效KP", normalized_name="失效KP",
        source_type="textbook_directory", review_status="approved",
    )
    session.add(orphan_kn)
    session.flush()
    q, _ = _make_question(session, [kns["ch1"].id, orphan_kn.id])

    res = resolve_question_chapter(session, q.id)
    assert res.status == "unresolvable"

    ids = {r.id for r in search_questions(
        query="", chapter_id=chapters["ch1"].id, limit=50, session=session)}
    assert q.id not in ids


# ---- CASE 10: ch1 KP (textbook A) + KP (textbook B) -> unresolvable, absent from ch1 filter ----
def test_cross_textbook_kp_unresolvable_and_absent_from_filter(session):
    _, chapters, kns = _seed_taxonomy(session)
    # kns["ch1"] 属教材甲（高等数学），kns["other"] 属教材乙（线性代数）。
    q, _ = _make_question(session, [kns["ch1"].id, kns["other"].id])

    res = resolve_question_chapter(session, q.id)
    assert res.status == "unresolvable"

    ids = {r.id for r in search_questions(
        query="", chapter_id=chapters["ch1"].id, limit=50, session=session)}
    assert q.id not in ids
