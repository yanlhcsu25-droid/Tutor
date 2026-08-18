"""Persistence model for conversation-scoped curriculum truth."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from calculus_agent.db import Base


class ConversationCurriculumContextRecord(Base):
    """Frozen curriculum-directory selection for one owner/conversation."""

    __tablename__ = "conversation_curriculum_context"

    owner_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    textbook_id: Mapped[str] = mapped_column(
        ForeignKey("textbook.id"),
        nullable=False,
        index=True,
    )
    directory_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    directory_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_by_run_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
