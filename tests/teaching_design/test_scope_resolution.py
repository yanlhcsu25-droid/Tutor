from calculus_agent.application.scope_resolution import (
    resolve_deterministic_scope_labels,
)
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def _book(session):
    book = Textbook(name="高数上", is_active=True)
    session.add(book)
    session.flush()
    chapter = CurriculumNode(
        textbook_id=book.id,
        node_type="chapter",
        code="1",
        title="函数与极限",
        sort_order=1,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    section = CurriculumNode(
        textbook_id=book.id,
        parent_id=chapter.id,
        node_type="section",
        code="1.1",
        title="函数的极限",
        sort_order=1,
        review_status="approved",
    )
    session.add(section)
    session.flush()
    session.add(KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="knowledge_point",
        name="极限运算",
        normalized_name="极限运算",
        review_status="approved",
    ))
    session.flush()


def test_explicit_chapter_aliases_share_one_boundary(session):
    _book(session)

    result = resolve_deterministic_scope_labels(
        session,
        ["高数第一章", "第一章", "函数与极限"],
    )

    assert result.ok is True
    assert result.validated_scope_names == ["第1章 函数与极限"]


def test_unknown_natural_language_is_not_sent_as_validated_scope(session):
    _book(session)

    result = resolve_deterministic_scope_labels(
        session,
        ["学生不理解 x 趋近 0"],
    )

    assert result.ok is False
    assert result.unresolved_labels == ["学生不理解 x 趋近 0"]
