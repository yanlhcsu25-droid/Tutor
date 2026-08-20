"""Conversation-scoped history of successful paper generation events."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import ConversationGenerationRecord, PaperItem


def historical_question_ids(
    session: Session,
    *,
    conversation_id: str,
) -> list[str]:
    """Return de-duplicated question IDs used by prior successful generations."""
    records = session.scalars(
        select(ConversationGenerationRecord.question_ids_json)
        .where(ConversationGenerationRecord.conversation_id == conversation_id)
        .order_by(ConversationGenerationRecord.created_at, ConversationGenerationRecord.id)
    )
    return list(dict.fromkeys(
        question_id
        for values in records
        for question_id in (values or [])
        if isinstance(question_id, str)
    ))


def record_successful_generation(
    session: Session,
    *,
    conversation_id: str,
    paper_id: str,
    version_id: str,
    teaching_design_version_id: str | None,
) -> ConversationGenerationRecord:
    """Persist the selected questions only after a Paper was successfully created."""
    question_ids = list(session.scalars(
        select(PaperItem.question_id)
        .where(PaperItem.paper_id == version_id)
        .order_by(PaperItem.position)
    ))
    record = ConversationGenerationRecord(
        conversation_id=conversation_id,
        paper_id=paper_id,
        teaching_design_version_id=teaching_design_version_id,
        question_ids_json=question_ids,
    )
    session.add(record)
    session.flush()
    return record
