"""Persistence models for the Agent state layer (Phase 1).

This is a NEW, isolated state infrastructure.  It establishes durable
per-conversation pointers and Agent lifecycle state WITHOUT changing existing
Agent behavior.  No Agent runtime, tool routing, prompt, Generation, or
WorkingMemory code depends on these tables yet.

Design rules (per the Phase 1 contract):

- ``ConversationWorkspace`` stores only **ID pointers**.  It never copies
  Paper content, ``GeneratePaperInput``, or any business JSON.
- ``AgentRuntimeState`` stores only the Agent lifecycle phase plus small
  string tags; it is not a state machine and holds no business payload.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from calculus_agent.db import Base


class ConversationWorkspace(Base):
    """Per-conversation pointers to the objects the conversation operates on."""

    __tablename__ = "conversation_workspace"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)

    active_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_paper_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pending_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AgentRuntimeState(Base):
    """Agent lifecycle state for one conversation."""

    __tablename__ = "agent_runtime_state"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)

    phase: Mapped[str] = mapped_column(String(20), default="idle")
    task_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    waiting_for: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
