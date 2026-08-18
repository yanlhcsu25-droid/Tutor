from calculus_agent.api import import_textbook_directory
from calculus_agent.curriculum_context import (
    ConversationCurriculumContextRecord,
    directory_fingerprint,
    resolve_conversation_curriculum_context,
    select_curriculum_context,
)
from calculus_agent.models import CurriculumNode, Textbook


DIRECTORY = """\
第一章 函数与极限
1.1 函数
1.1.1 函数概念
"""


def test_first_import_keeps_revision_one_and_replacement_increments(session):
    book = Textbook(id="book-a", name="教材 A")
    session.add(book)
    session.flush()

    import_textbook_directory(
        book.id,
        {"text": DIRECTORY, "replace": True},
        session=session,
    )
    assert book.directory_revision == 1
    first_fingerprint = directory_fingerprint(session, book.id)

    import_textbook_directory(
        book.id,
        {"text": DIRECTORY, "replace": True},
        session=session,
    )
    assert book.directory_revision == 2
    second_fingerprint = directory_fingerprint(session, book.id)

    assert first_fingerprint == second_fingerprint


def test_fingerprint_changes_when_business_directory_content_changes(session):
    book = Textbook(id="book-a", name="教材 A")
    session.add(book)
    session.flush()

    chapter = CurriculumNode(
        id="chapter-old-id",
        textbook_id=book.id,
        parent_id=None,
        node_type="chapter",
        code="一",
        title="函数与极限",
        sort_order=0,
    )
    section = CurriculumNode(
        id="section-old-id",
        textbook_id=book.id,
        parent_id=chapter.id,
        node_type="section",
        code="1.1",
        title="函数",
        sort_order=1,
    )
    session.add_all([chapter, section])
    session.flush()

    before = directory_fingerprint(session, book.id)
    section.title = "函数及其性质"
    session.flush()
    after = directory_fingerprint(session, book.id)

    assert before != after


def test_context_becomes_stale_after_directory_replacement_without_rebinding(session):
    book = Textbook(id="book-a", name="教材 A", is_active=True)
    session.add(book)
    session.flush()

    import_textbook_directory(
        book.id,
        {"text": DIRECTORY, "replace": True},
        session=session,
    )
    selected = select_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-a",
        textbook_id=book.id,
    )
    assert selected.stale is False
    assert selected.directory_revision == 1

    import_textbook_directory(
        book.id,
        {
            "text": DIRECTORY.replace("函数概念", "函数定义"),
            "replace": True,
        },
        session=session,
    )
    assert book.directory_revision == 2

    resolved = resolve_conversation_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-a",
    )
    assert resolved.context is not None
    assert resolved.context.stale is True
    assert "directory_revision_changed" in resolved.context.stale_reasons
    assert "directory_fingerprint_changed" in resolved.context.stale_reasons

    record = session.get(
        ConversationCurriculumContextRecord,
        {"owner_key": "local-teacher", "conversation_id": "conv-a"},
    )
    assert record.directory_revision == 1
    assert record.directory_fingerprint == selected.directory_fingerprint
