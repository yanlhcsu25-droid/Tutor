"""题库抽屉 (/api/v1/questions/search) 按大章节筛选测试。

覆盖 Case 1-7：
1. chapter_id=None → 返回全部
2. 题目知识点属于第一章子节点 → 筛选第一章返回
3. 题目属于第二章 → 筛选第一章不返回
4. 一道题绑定多个第一章知识点 → 结果只出现一次
5. 一道题跨第一章和第二章 → 仅出现在最靠后章节（第二章）筛选，不出现在第一章
6. 章节 + 已发布来源 → AND 语义
7. 切回全部章节 → 恢复原始行为
"""
from __future__ import annotations

from calculus_agent.api import import_textbook_directory, search_questions
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.questions.chapter_assignment import (
    sync_question_chapter_ownership,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)

DIRECTORY_TEXT = """
第一章 函数与极限
1.1 映射与函数
1.2 数列的极限
1.3 函数的极限
第二章 导数与微分
2.1 导数定义
2.2 求导法则
"""


def _seed_taxonomy(session) -> tuple[Textbook, CurriculumNode, CurriculumNode]:
    textbook = Textbook(name="测试教材", is_active=True)
    session.add(textbook)
    session.flush()
    import_textbook_directory(textbook.id, {"text": DIRECTORY_TEXT, "replace": True}, session)
    session.flush()

    chapters = list(session.scalars(
        __import__("sqlalchemy").select(CurriculumNode)
        .where(
            CurriculumNode.textbook_id == textbook.id,
            CurriculumNode.node_type == "chapter",
        )
        .order_by(CurriculumNode.sort_order)
    ).all())
    assert len(chapters) == 2, f"expected 2 chapters, got {len(chapters)}"
    return textbook, chapters[0], chapters[1]


def _knowledge_for_chapter(
    session, chapter: CurriculumNode, name: str
) -> KnowledgeNode:
    """在指定章下找一个 section 节点，并创建一个已审核 KnowledgeNode 挂到该 section。

    为避免与目录导入自动生成的知识点冲突，name 使用测试专属前缀。
    """
    sections = list(session.scalars(
        __import__("sqlalchemy").select(CurriculumNode)
        .where(
            CurriculumNode.parent_id == chapter.id,
            CurriculumNode.node_type.in_(("section", "knowledge_point")),
        )
        .order_by(CurriculumNode.sort_order)
    ).all())
    assert sections, f"chapter {chapter.title} has no sections"
    unique_name = f"{name} (test-{sections[0].id[:8]})"
    node = KnowledgeNode(
        name=unique_name,
        normalized_name=normalize_name(unique_name),
        node_type="concept",
        curriculum_node_id=sections[0].id,
        review_status="approved",
    )
    session.add(node)
    session.flush()
    return node


def _add_question(
    session,
    question_id: str,
    knowledge_nodes: list[KnowledgeNode],
    *,
    source_name: str = "ocr_import",
) -> Question:
    draft = QuestionDraft(
        id=f"draft-{question_id}",
        source_name=source_name,
        source_item_id=question_id,
        variant=1,
        subject="高等数学",
        question_type="calculation",
        question_text=f"题干-{question_id}",
        reference_answers_json=[],
        normalized_fingerprint=question_id.replace("-", "")[:32].ljust(64, "0"),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        id=question_id,
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type="calculation",
        solution_json={},
        verification_status="verified",
        review_status="approved",
        publish_source="manual",
    )
    session.add(question)
    session.flush()
    for idx, kn in enumerate(knowledge_nodes):
        session.add(QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=kn.id,
            relation_type="primary" if idx == 0 else "secondary",
        ))
    session.flush()
    # Tests insert QuestionKnowledgeLink rows directly, bypassing production
    # services. Materialize the same ownership those services synchronize.
    sync_question_chapter_ownership(session, question.id)
    return question


def _search(session, *, chapter_id: str | None = None, source_name: str | None = "ocr_import"):
    return search_questions(
        query="",
        question_type=None,
        source_name=source_name,
        publish_source=None,
        chapter_id=chapter_id,
        limit=50,
        session=session,
    )


def test_chapter_filter_none_returns_all(session):
    _, c1, c2 = _seed_taxonomy(session)
    k1 = _knowledge_for_chapter(session, c1, "函数极限")
    k2 = _knowledge_for_chapter(session, c2, "导数定义")
    _add_question(session, "q-11111111-1111-4111-a111-111111111111", [k1])
    _add_question(session, "q-22222222-2222-4222-a222-222222222222", [k2])

    results = _search(session, chapter_id=None)
    ids = {r.id for r in results}
    assert ids == {"q-11111111-1111-4111-a111-111111111111", "q-22222222-2222-4222-a222-222222222222"}


def test_chapter_filter_child_knowledge_matches(session):
    _, c1, _ = _seed_taxonomy(session)
    k1 = _knowledge_for_chapter(session, c1, "函数极限")
    _add_question(session, "q-11111111-1111-4111-a111-111111111111", [k1])

    results = _search(session, chapter_id=c1.id)
    assert [r.id for r in results] == ["q-11111111-1111-4111-a111-111111111111"]


def test_chapter_filter_other_chapter_excluded(session):
    _, c1, c2 = _seed_taxonomy(session)
    k2 = _knowledge_for_chapter(session, c2, "导数定义")
    _add_question(session, "q-22222222-2222-4222-a222-222222222222", [k2])

    results = _search(session, chapter_id=c1.id)
    assert results == []


def test_chapter_filter_deduplicates_multiple_same_chapter_knowledge(session):
    _, c1, _ = _seed_taxonomy(session)
    k1a = _knowledge_for_chapter(session, c1, "函数极限 A")
    k1b = _knowledge_for_chapter(session, c1, "函数极限 B")
    _add_question(session, "q-11111111-1111-4111-a111-111111111111", [k1a, k1b])

    results = _search(session, chapter_id=c1.id)
    assert [r.id for r in results] == ["q-11111111-1111-4111-a111-111111111111"]


def test_chapter_filter_cross_chapter_question_appears_only_in_latest(session):
    """跨章题目（第一章 + 第二章）的派生章节为最靠后的第二章，
    因此只应出现在第二章筛选，绝不应出现在第一章筛选。"""
    _, c1, c2 = _seed_taxonomy(session)
    k1 = _knowledge_for_chapter(session, c1, "函数极限")
    k2 = _knowledge_for_chapter(session, c2, "导数定义")
    _add_question(session, "q-33333333-3333-4333-a333-333333333333", [k1, k2])

    assert [r.id for r in _search(session, chapter_id=c1.id)] == []
    assert [r.id for r in _search(session, chapter_id=c2.id)] == ["q-33333333-3333-4333-a333-333333333333"]


def test_chapter_filter_cross_ch1_ch3_appears_only_in_ch3(session):
    """用户场景：一题同时有第一章 + 第三章知识点 → 派生章节为第三章。
    筛选第一章 → 不能出现；筛选第三章 → 必须出现。"""
    import uuid

    textbook, c1, _ = _seed_taxonomy(session)
    c3 = CurriculumNode(
        id=f"ch3-{uuid.uuid4().hex[:8]}",
        textbook_id=textbook.id,
        parent_id=None,
        node_type="chapter",
        code="三",
        title="第三章 微分中值定理",
        sort_order=30,
        review_status="approved",
    )
    session.add(c3)
    session.flush()
    s3 = CurriculumNode(
        id=f"s3-{uuid.uuid4().hex[:8]}",
        textbook_id=textbook.id,
        parent_id=c3.id,
        node_type="section",
        title="3.1 洛必达法则",
        sort_order=31,
        review_status="approved",
    )
    session.add(s3)
    session.flush()
    k1 = _knowledge_for_chapter(session, c1, "函数极限")
    k3 = KnowledgeNode(
        name=f"洛必达 (test-{s3.id[:8]})",
        normalized_name=normalize_name(f"洛必达 (test-{s3.id[:8]})"),
        node_type="concept",
        curriculum_node_id=s3.id,
        review_status="approved",
    )
    session.add(k3)
    session.flush()
    _add_question(session, "q-ch1ch3-1111-4111-a111-111111111111", [k1, k3])

    # 筛选第一章：不能出现
    assert [r.id for r in _search(session, chapter_id=c1.id)] == []
    # 筛选第三章：必须出现
    assert [r.id for r in _search(session, chapter_id=c3.id)] == [
        "q-ch1ch3-1111-4111-a111-111111111111"
    ]


def test_chapter_filter_combines_with_source_name_and(session):
    _, c1, _ = _seed_taxonomy(session)
    k1 = _knowledge_for_chapter(session, c1, "函数极限")
    _add_question(session, "q-11111111-1111-4111-a111-111111111111", [k1], source_name="ocr_import")
    _add_question(session, "q-22222222-2222-4222-a222-222222222222", [k1], source_name="built-in-demo")

    results = _search(session, chapter_id=c1.id, source_name="ocr_import")
    assert [r.id for r in results] == ["q-11111111-1111-4111-a111-111111111111"]


def test_chapter_filter_all_restores_unfiltered(session):
    _, c1, c2 = _seed_taxonomy(session)
    k1 = _knowledge_for_chapter(session, c1, "函数极限")
    k2 = _knowledge_for_chapter(session, c2, "导数定义")
    _add_question(session, "q-11111111-1111-4111-a111-111111111111", [k1])
    _add_question(session, "q-22222222-2222-4222-a222-222222222222", [k2])

    filtered = _search(session, chapter_id=c1.id)
    assert {r.id for r in filtered} == {"q-11111111-1111-4111-a111-111111111111"}

    restored = _search(session, chapter_id=None)
    assert {r.id for r in restored} == {
        "q-11111111-1111-4111-a111-111111111111",
        "q-22222222-2222-4222-a222-222222222222",
    }
