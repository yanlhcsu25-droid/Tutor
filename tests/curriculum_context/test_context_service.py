from calculus_agent.curriculum_context import (
    ConversationCurriculumContextRecord,
    resolve_conversation_curriculum_context,
    select_curriculum_context,
)
from calculus_agent.models import Textbook


def _book(
    session,
    *,
    book_id: str,
    name: str,
    active: bool = False,
    edition: str | None = None,
) -> Textbook:
    book = Textbook(
        id=book_id,
        name=name,
        edition=edition,
        is_active=active,
    )
    session.add(book)
    session.flush()
    return book


def test_two_conversations_can_bind_different_textbooks(session):
    left = _book(session, book_id="book-a", name="教材 A")
    right = _book(session, book_id="book-b", name="教材 B")

    context_a = select_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-a",
        textbook_id=left.id,
    )
    context_b = select_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-b",
        textbook_id=right.id,
    )

    assert context_a.textbook_id == "book-a"
    assert context_b.textbook_id == "book-b"

    record_a = session.get(
        ConversationCurriculumContextRecord,
        {"owner_key": "local-teacher", "conversation_id": "conv-a"},
    )
    record_b = session.get(
        ConversationCurriculumContextRecord,
        {"owner_key": "local-teacher", "conversation_id": "conv-b"},
    )
    assert record_a.textbook_id == "book-a"
    assert record_b.textbook_id == "book-b"


def test_existing_context_does_not_follow_global_active_textbook(session):
    left = _book(
        session,
        book_id="book-a",
        name="教材 A",
        active=True,
    )
    right = _book(
        session,
        book_id="book-b",
        name="教材 B",
        active=False,
    )

    first = resolve_conversation_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-existing",
    )
    assert first.error_code is None
    assert first.context is not None
    assert first.context.textbook_id == left.id

    left.is_active = False
    right.is_active = True
    session.flush()

    existing = resolve_conversation_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-existing",
    )
    new_conversation = resolve_conversation_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-new",
    )

    assert existing.context is not None
    assert existing.context.textbook_id == left.id
    assert new_conversation.context is not None
    assert new_conversation.context.textbook_id == right.id


def test_no_unique_active_textbook_returns_no_curriculum_context(session):
    result_without_active = resolve_conversation_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-none",
    )
    assert result_without_active.context is None
    assert result_without_active.error_code == "no_curriculum_context"

    _book(session, book_id="book-a", name="教材 A", active=True)
    _book(session, book_id="book-b", name="教材 B", active=True)

    result_ambiguous = resolve_conversation_curriculum_context(
        session,
        owner_key="local-teacher",
        conversation_id="conv-many",
    )
    assert result_ambiguous.context is None
    assert result_ambiguous.error_code == "no_curriculum_context"
