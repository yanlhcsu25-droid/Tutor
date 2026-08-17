"""Regression tests for the "重新导入目录" (re-import full directory) flow.

Covers the Preview (read-only) + Confirm (transactional replace) path and the
strict hierarchy validator. The Chapter -> Section -> KnowledgePoint parser fix
itself is covered by tests/test_curriculum_directory_hierarchy.py and is NOT
re-tested here beyond the regression run.
"""

from sqlalchemy import select

import pytest

from calculus_agent.api import import_textbook_directory, preview_textbook_directory
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)


OLD_TEXT = (
    "第三章 微分中值定理与导数的应用\n"
    "3.1 微分中值定理\n"
    "罗尔定理\n"
    "拉格朗日中值定理\n"
    "3.2 洛必达法则\n"
    "0/0型未定式\n"
    "∞/∞型未定式"
)
NEW_TEXT = (
    "第四章 多元函数微分学\n"
    "4.1 偏导数\n"
    "偏导数定义\n"
    "4.2 全微分\n"
    "全微分定义"
)


def _book(session, name: str = "高等数学上") -> Textbook:
    book = Textbook(name=name, is_active=True)
    session.add(book)
    session.flush()
    return book


def _ordered(session, book_id):
    return list(
        session.scalars(
            select(CurriculumNode)
            .where(CurriculumNode.textbook_id == book_id)
            .order_by(CurriculumNode.sort_order)
        ).all()
    )


def test_preview_does_not_write_to_database(session) -> None:
    book = _book(session)
    import_textbook_directory(book.id, {"text": OLD_TEXT, "replace": True}, session)
    before = session.scalars(
        select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
    ).all()
    before_count = len(before)
    before_kp = len([n for n in before if n.node_type == "knowledge_point"])

    # Preview must be read-only.
    result = preview_textbook_directory(book.id, {"text": NEW_TEXT}, session)
    assert result["valid"] is True

    after = session.scalars(
        select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
    ).all()
    assert len(after) == before_count
    assert len([n for n in after if n.node_type == "knowledge_point"]) == before_kp
    # The previewed (new) title must not appear until Confirm.
    assert not any(n.title == "多元函数微分学" for n in after)


def test_preview_returns_correct_tree(session) -> None:
    book = _book(session)
    result = preview_textbook_directory(book.id, {"text": OLD_TEXT}, session)
    assert result["valid"] is True
    assert result["statistics"] == {"chapters": 1, "sections": 2, "knowledge_points": 4}

    tree = result["tree"]
    assert len(tree) == 1
    chapter = tree[0]
    assert chapter["type"] == "chapter"
    assert len(chapter["children"]) == 2

    s31, s32 = chapter["children"]
    assert s31["type"] == "section"
    assert {c["title"] for c in s31["children"]} == {"罗尔定理", "拉格朗日中值定理"}
    assert {c["title"] for c in s32["children"]} == {"0/0型未定式", "∞/∞型未定式"}
    assert all(c["parent_id"] == s31["id"] for c in s31["children"])
    assert all(c["parent_id"] == s32["id"] for c in s32["children"])


def test_strict_hierarchy_rejects_knowledge_point_without_section(session) -> None:
    book = _book(session)
    result = preview_textbook_directory(
        book.id,
        {"text": "第三章 微分中值定理与导数的应用\n罗尔定理"},
        session,
    )
    assert result["valid"] is False
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err["code"] == "knowledge_point_without_section"
    assert err["line"] == 2
    assert "罗尔定理" in err["message"]


def test_confirm_executes_replace(session) -> None:
    book = _book(session)
    import_textbook_directory(book.id, {"text": OLD_TEXT, "replace": True}, session)
    preview = preview_textbook_directory(book.id, {"text": NEW_TEXT}, session)
    assert preview["valid"] is True

    import_textbook_directory(
        book.id, {"text": NEW_TEXT, "replace": True, "strict": True}, session
    )
    nodes = _ordered(session, book.id)
    chapters = [n for n in nodes if n.node_type == "chapter"]
    sections = [n for n in nodes if n.node_type == "section"]
    kps = [n for n in nodes if n.node_type == "knowledge_point"]
    # Chapter title is the part after "第四章 " (code is "四").
    assert len(chapters) == 1 and chapters[0].title == "多元函数微分学"
    assert len(sections) == 2 and len(kps) == 2
    # Old directory fully replaced.
    assert not any(n.title == "微分中值定理与导数的应用" for n in nodes)


def test_confirm_rolls_back_on_sync_failure(tmp_path, monkeypatch) -> None:
    """A failed Confirm must leave the pre-request directory fully intact."""
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_schema(database_url)
    factory = build_session_factory(database_url)

    # Session A: seed the existing directory and commit (pre-request state).
    with factory.begin() as sa:
        book = Textbook(name="x", is_active=True)
        sa.add(book)
        sa.flush()
        import_textbook_directory(book.id, {"text": OLD_TEXT, "replace": True}, sa)
        old_ids = {
            n.id
            for n in sa.scalars(
                select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
            ).all()
        }

    # Session B: the Confirm request fails mid-sync -> must roll back.
    def _boom(*args, **kwargs):
        raise RuntimeError("sync failed")

    monkeypatch.setattr("calculus_agent.api.sync_directory_knowledge_nodes", _boom)
    # Let the exception propagate out of factory.begin() so it rolls back,
    # then assert it was raised.
    with pytest.raises(RuntimeError):
        with factory.begin() as sb:
            import_textbook_directory(
                book.id, {"text": NEW_TEXT, "replace": True, "strict": True}, sb
            )

    # Session C: verify the database still holds exactly the old directory.
    with factory.begin() as sc:
        remaining = sc.scalars(
            select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
        ).all()
        assert {n.id for n in remaining} == old_ids
        assert not any(n.title.startswith("第四章") for n in remaining)


def test_idempotent_double_confirm(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id, {"text": OLD_TEXT, "replace": True, "strict": True}, session
    )
    first = session.scalars(
        select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
    ).all()
    first_counts = (
        len([n for n in first if n.node_type == "chapter"]),
        len([n for n in first if n.node_type == "section"]),
        len([n for n in first if n.node_type == "knowledge_point"]),
    )
    first_kp_ids = {
        k.id
        for k in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.source_type == "directory")
        ).all()
    }

    import_textbook_directory(
        book.id, {"text": OLD_TEXT, "replace": True, "strict": True}, session
    )
    second = session.scalars(
        select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
    ).all()
    second_counts = (
        len([n for n in second if n.node_type == "chapter"]),
        len([n for n in second if n.node_type == "section"]),
        len([n for n in second if n.node_type == "knowledge_point"]),
    )
    second_kp_ids = {
        k.id
        for k in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.source_type == "directory")
        ).all()
    }

    assert first_counts == second_counts
    # Directory-backed knowledge nodes are reused, not duplicated.
    assert second_kp_ids == first_kp_ids


def test_existing_question_knowledge_link_survives_reimport(session) -> None:
    book = _book(session)
    import_textbook_directory(
        book.id, {"text": OLD_TEXT, "replace": True, "strict": True}, session
    )
    kp_node = session.scalars(
        select(CurriculumNode).where(
            CurriculumNode.textbook_id == book.id,
            CurriculumNode.title == "罗尔定理",
        )
    ).one()
    knowledge = session.scalars(
        select(KnowledgeNode).where(KnowledgeNode.curriculum_node_id == kp_node.id)
    ).one()
    original_knowledge_id = knowledge.id
    assert knowledge.review_status == "approved"

    draft = QuestionDraft(
        source_name="reimport-test",
        source_item_id="q1",
        variant=1,
        subject="高数",
        question_type="计算题",
        question_text="证明罗尔定理",
        normalized_fingerprint="0" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text="证明罗尔定理",
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary",
        )
    )

    import_textbook_directory(
        book.id, {"text": OLD_TEXT, "replace": True, "strict": True}, session
    )

    link = session.get(
        QuestionKnowledgeLink,
        session.scalar(
            select(QuestionKnowledgeLink.id).where(
                QuestionKnowledgeLink.question_id == question.id
            )
        ),
    )
    assert link is not None
    reused = session.get(KnowledgeNode, link.knowledge_node_id)
    assert reused is not None
    assert reused.id == original_knowledge_id
    assert reused.review_status == "approved"
