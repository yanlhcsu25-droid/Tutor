from sqlalchemy import select

from calculus_agent.api import (
    import_textbook_directory,
    update_textbook_node,
)
from calculus_agent.knowledge.classification import current_textbook_taxonomy
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def _book(session, name: str, *, active: bool = False) -> Textbook:
    book = Textbook(name=name, is_active=active)
    session.add(book)
    session.flush()
    return book


def test_directory_import_creates_approved_taxonomy_for_active_textbook(session) -> None:
    book = _book(session, "高等数学下", active=True)

    result = import_textbook_directory(
        book.id,
        {"text": "第八章 向量代数\n8.1 向量及其线性运算\n8.2 数量积", "replace": True},
        session,
    )

    assert result == {"imported_count": 3, "knowledge_count": 3}
    assert [item.name for item in current_textbook_taxonomy(session)] == [
        "向量代数",
        "向量及其线性运算",
        "数量积",
    ]
    assert {item.review_status for item in current_textbook_taxonomy(session)} == {"approved"}


def test_replacing_directory_preserves_matching_ids_and_retires_removed_entries(session) -> None:
    book = _book(session, "高等数学下", active=True)
    import_textbook_directory(
        book.id,
        {"text": "第八章 向量代数\n8.1 向量\n8.2 数量积", "replace": True},
        session,
    )
    before = {item.name: item.id for item in current_textbook_taxonomy(session)}

    import_textbook_directory(
        book.id,
        {"text": "第八章 向量代数\n8.1 向量\n8.3 平面方程", "replace": True},
        session,
    )

    after = {item.name: item.id for item in current_textbook_taxonomy(session)}
    assert after["向量代数"] == before["向量代数"]
    assert after["向量"] == before["向量"]
    removed = session.get(KnowledgeNode, before["数量积"])
    assert removed is not None
    assert removed.review_status == "retired"
    assert removed.curriculum_node_id is None


def test_same_directory_title_is_scoped_per_textbook(session) -> None:
    first = _book(session, "上册", active=True)
    second = _book(session, "下册")
    import_textbook_directory(first.id, {"text": "第一章 公共标题", "replace": True}, session)
    import_textbook_directory(second.id, {"text": "第一章 公共标题", "replace": True}, session)

    nodes = list(session.scalars(
        select(KnowledgeNode).where(KnowledgeNode.name == "公共标题")
    ).all())
    assert len(nodes) == 2
    assert nodes[0].id != nodes[1].id


def test_directory_title_edit_updates_review_knowledge_name(session) -> None:
    book = _book(session, "高等数学下", active=True)
    import_textbook_directory(book.id, {"text": "第八章 向量代数", "replace": True}, session)
    curriculum = session.scalar(
        select(CurriculumNode).where(CurriculumNode.textbook_id == book.id)
    )
    assert curriculum is not None

    update_textbook_node(curriculum.id, {"title": "向量代数与空间解析几何"}, session)

    taxonomy = current_textbook_taxonomy(session)
    assert [item.name for item in taxonomy] == ["向量代数与空间解析几何"]
