"""Regression tests for textbook directory hierarchy.

Bug fixed: knowledge points were attached directly to the chapter instead of
to the nearest section, because the importer only tracked the current chapter
id and never recorded the current section id.

Covers both directory parsers that share this defect:
- ``_parse_directory_text`` (api.py) used by ``import_textbook_directory``
- ``import_curriculum`` (knowledge/curriculum.py) used by ``/curriculum/import``
"""

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from calculus_agent.api import import_textbook_directory, _preview_directory
from calculus_agent.knowledge.curriculum import import_curriculum
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def _book(session: Session, name: str = "高等数学上", active: bool = True) -> Textbook:
    book = Textbook(name=name, is_active=active)
    session.add(book)
    session.flush()
    return book


def _ordered(session, book_id=None):
    q = select(CurriculumNode)
    if book_id is not None:
        q = q.where(CurriculumNode.textbook_id == book_id)
    return list(session.scalars(q.order_by(CurriculumNode.sort_order)).all())


def test_chapter_section_knowledgepoint_hierarchy(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {
            "text": (
                "第三章 微分中值定理与导数的应用\n"
                "3.1 微分中值定理\n"
                "罗尔定理\n"
                "拉格朗日中值定理\n"
                "3.2 洛必达法则\n"
                "0/0型未定式\n"
                "∞/∞型未定式"
            ),
            "replace": True,
        },
        session,
    )
    nodes = _ordered(session, book.id)
    chapters = [n for n in nodes if n.node_type == "chapter"]
    sections = [n for n in nodes if n.node_type == "section"]
    kps = [n for n in nodes if n.node_type == "knowledge_point"]

    assert len(chapters) == 1 and len(sections) == 2 and len(kps) == 4
    chapter = chapters[0]
    s31, s32 = sections

    assert chapter.parent_id is None
    # Both sections belong to the chapter.
    assert s31.parent_id == chapter.id
    assert s32.parent_id == chapter.id
    # Knowledge points belong to the section that precedes them, NOT the chapter.
    assert kps[0].parent_id == s31.id
    assert kps[1].parent_id == s31.id
    assert kps[2].parent_id == s32.id
    assert kps[3].parent_id == s32.id
    assert all(kp.parent_id != chapter.id for kp in kps)


def test_switching_section_keeps_points_under_correct_section(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {
            "text": (
                "第三章 微分中值定理与导数的应用\n"
                "3.1 微分中值定理\n"
                "知识点X\n"
                "3.2 洛必达法则\n"
                "知识点Y"
            ),
            "replace": True,
        },
        session,
    )
    nodes = _ordered(session, book.id)
    sections = [n for n in nodes if n.node_type == "section"]
    kps = [n for n in nodes if n.node_type == "knowledge_point"]
    assert len(sections) == 2 and len(kps) == 2

    # 知识点X is after 3.1, 知识点Y after 3.2 — they must not swap sections.
    assert kps[0].parent_id == sections[0].id
    assert kps[1].parent_id == sections[1].id
    assert kps[0].parent_id != sections[1].id


def test_switching_chapter_resets_current_section(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {
            "text": (
                "第三章 微分中值定理与导数的应用\n"
                "3.1 微分中值定理\n"
                "知识点A\n"
                "第四章 多元函数微分学\n"
                "4.1 偏导数\n"
                "知识点B"
            ),
            "replace": True,
        },
        session,
    )
    nodes = _ordered(session, book.id)
    chapters = [n for n in nodes if n.node_type == "chapter"]
    sections = [n for n in nodes if n.node_type == "section"]
    kps = [n for n in nodes if n.node_type == "knowledge_point"]
    assert len(chapters) == 2 and len(sections) == 2 and len(kps) == 2

    ch3, ch4 = chapters
    s31, s41 = sections
    # Entering a new chapter must clear the previous section pointer.
    assert s31.parent_id == ch3.id
    assert s41.parent_id == ch4.id
    assert kps[0].parent_id == s31.id
    assert kps[1].parent_id == s41.id


def test_import_curriculum_topic_hierarchy(session) -> None:
    # import_curriculum uses node_type "topic" and keeps the full line as title.
    import_curriculum(
        session,
        "第三章 微分中值定理与导数的应用\n"
        "3.1 微分中值定理\n"
        "罗尔定理\n"
        "拉格朗日中值定理\n"
        "3.2 洛必达法则\n"
        "0/0型未定式\n"
        "∞/∞型未定式",
    )
    nodes = _ordered(session)
    chapters = [n for n in nodes if n.node_type == "chapter"]
    sections = [n for n in nodes if n.node_type == "section"]
    topics = [n for n in nodes if n.node_type == "topic"]
    assert len(chapters) == 1 and len(sections) == 2 and len(topics) == 4

    chapter = chapters[0]
    s31, s32 = sections
    assert chapter.parent_id is None
    assert s31.parent_id == chapter.id
    assert s32.parent_id == chapter.id
    assert topics[0].parent_id == s31.id
    assert topics[1].parent_id == s31.id
    assert topics[2].parent_id == s32.id
    assert topics[3].parent_id == s32.id


# ── 三级编号支持（X.Y.Z → KnowledgePoint）──


def _by_code(nodes, book_id=None):
    q = [n for n in nodes if n.code]
    if book_id is not None:
        q = [n for n in q if n.textbook_id == book_id]
    return {n.code: n for n in q}


def test_three_level_knowledge_point_recognition(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {"text": (
            "第三章 微分中值定理与导数的应用\n"
            "3.1 微分中值定理\n"
            "3.1.1 罗尔定理\n"
            "3.1.2 拉格朗日中值定理\n"
            "3.1.3 柯西中值定理\n"
        ), "replace": True},
        session,
    )
    by_code = _by_code(_ordered(session, book.id), book.id)
    s31 = by_code["3.1"]
    assert s31.node_type == "section"
    for code in ("3.1.1", "3.1.2", "3.1.3"):
        kp = by_code[code]
        assert kp.node_type == "knowledge_point"
        assert kp.parent_id == s31.id


def test_three_level_code_preserved(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {"text": (
            "第三章 微分中值定理与导数的应用\n"
            "3.1 微分中值定理\n"
            "3.1.1 罗尔定理\n"
            "3.1.2 拉格朗日中值定理\n"
        ), "replace": True},
        session,
    )
    by_code = _by_code(_ordered(session, book.id), book.id)
    assert by_code["3.1.1"].code == "3.1.1"
    assert by_code["3.1.2"].code == "3.1.2"
    assert by_code["3.1.1"].title == "罗尔定理"
    assert by_code["3.1.2"].title == "拉格朗日中值定理"
    assert "3.1.1" not in by_code["3.1.1"].title


def test_three_level_switching_section(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {"text": (
            "第三章 微分中值定理与导数的应用\n"
            "3.1 微分中值定理\n"
            "3.1.1 罗尔定理\n"
            "3.1.2 拉格朗日中值定理\n"
            "3.2 洛必达法则\n"
            "3.2.1 0/0 型未定式\n"
            "3.2.2 ∞/∞ 型未定式\n"
        ), "replace": True},
        session,
    )
    by_code = _by_code(_ordered(session, book.id), book.id)
    assert by_code["3.1.1"].parent_id == by_code["3.1"].id
    assert by_code["3.1.2"].parent_id == by_code["3.1"].id
    assert by_code["3.2.1"].parent_id == by_code["3.2"].id
    assert by_code["3.2.2"].parent_id == by_code["3.2"].id


def test_knowledge_point_section_mismatch(session) -> None:
    book = _book(session)
    result = _preview_directory(book.id, (
        "第三章 微分中值定理与导数的应用\n"
        "3.1 微分中值定理\n"
        "3.2.1 洛必达法则\n"
    ))
    assert not result["valid"]
    mismatch = [e for e in result["errors"] if e["code"] == "knowledge_point_section_mismatch"]
    assert mismatch, result["errors"]
    assert mismatch[0]["line"] == 3
    assert "3.2" in mismatch[0]["message"] and "3.1" in mismatch[0]["message"]


def test_legacy_unnumbered_knowledge_point_compatible(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id,
        {"text": (
            "第三章 微分中值定理与导数的应用\n"
            "3.1 微分中值定理\n"
            "罗尔定理\n"
            "拉格朗日中值定理\n"
        ), "replace": True},
        session,
    )
    nodes = _ordered(session, book.id)
    by_title = {n.title: n for n in nodes}
    s31 = by_title["微分中值定理"]
    kp_roll = by_title["罗尔定理"]
    kp_lag = by_title["拉格朗日中值定理"]
    assert s31.node_type == "section"
    assert kp_roll.node_type == "knowledge_point"
    assert kp_lag.node_type == "knowledge_point"
    assert kp_roll.parent_id == s31.id
    assert kp_lag.parent_id == s31.id
    assert kp_roll.code is None


def test_fourth_level_rejected(session) -> None:
    book = _book(session)
    result = _preview_directory(book.id, (
        "第三章 微分中值定理与导数的应用\n"
        "3.1 微分中值定理\n"
        "3.1.1 罗尔定理\n"
        "3.1.1.1 罗尔定理证明存在点\n"
    ))
    assert not result["valid"]
    deep = [e for e in result["errors"] if e["code"] == "unsupported_directory_depth"]
    assert deep, result["errors"]
    assert deep[0]["line"] == 4


def test_preview_read_only_with_three_level(session) -> None:
    book = _book(session)
    before_nodes = len(_ordered(session, book.id))
    before_kp = session.scalar(select(func.count(KnowledgeNode.id)))
    result = _preview_directory(book.id, (
        "第三章 微分中值定理与导数的应用\n"
        "3.1 微分中值定理\n"
        "3.1.1 罗尔定理\n"
        "3.1.2 拉格朗日中值定理\n"
    ))
    assert result["valid"]
    after_nodes = len(_ordered(session, book.id))
    after_kp = session.scalar(select(func.count(KnowledgeNode.id)))
    assert after_nodes == before_nodes
    assert after_kp == before_kp


def test_confirm_persists_three_level(session) -> None:
    book = _book(session)
    # 模拟 UI 的 Confirm 入口：strict=True 先校验再落库
    import_textbook_directory(
        book.id,
        {"text": (
            "第三章 微分中值定理与导数的应用\n"
            "3.1 微分中值定理\n"
            "3.1.1 罗尔定理\n"
            "3.1.2 拉格朗日中值定理\n"
        ), "replace": True, "strict": True},
        session,
    )
    by_code = _by_code(_ordered(session, book.id), book.id)
    assert by_code["3.1"].node_type == "section"
    assert by_code["3.1.1"].node_type == "knowledge_point"
    assert by_code["3.1.2"].node_type == "knowledge_point"
    assert by_code["3.1.1"].parent_id == by_code["3.1"].id
    assert by_code["3.1.2"].parent_id == by_code["3.1"].id


def test_import_curriculum_three_level_topic(session) -> None:
    # 旧 /curriculum/import 端点也需避免把 X.Y.Z 误判为节
    import_curriculum(session, (
        "第三章 微分中值定理与导数的应用\n"
        "3.1 微分中值定理\n"
        "3.1.1 罗尔定理\n"
        "3.1.2 拉格朗日中值定理\n"
        "3.2 洛必达法则\n"
        "3.2.1 0/0 型未定式\n"
    ))
    nodes = _ordered(session)
    sections = [n for n in nodes if n.node_type == "section"]
    s31 = next(s for s in sections if s.title.startswith("3.1 "))
    s32 = next(s for s in sections if s.title.startswith("3.2 "))
    by_code = _by_code(nodes)
    assert by_code["3.1.1"].node_type == "topic"
    assert by_code["3.1.2"].node_type == "topic"
    assert by_code["3.1.1"].parent_id == s31.id
    assert by_code["3.2.1"].parent_id == s32.id
