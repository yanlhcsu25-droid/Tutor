"""SQLAlchemy persistence models for the TeachingDesign domain.

These tables live outside ``calculus_agent.models`` so the monolithic legacy
ORM module does not keep growing with every new business capability.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from calculus_agent.db import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class TeachingDesignVersionRecord(Base):
    __tablename__ = "teaching_design_version"
    __table_args__ = (
        UniqueConstraint("design_key", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)

    # Stable identity shared by every immutable version of the same design.
    design_key: Mapped[str] = mapped_column(String(36), index=True)
    owner_key: Mapped[str] = mapped_column(String(120), index=True)
    source_conversation_id: Mapped[str] = mapped_column(String(120), index=True)

    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("teaching_design_version.id"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(30),
        default="draft",
        index=True,
    )

    # Search projection + immutable business content snapshot.
    title: Mapped[str] = mapped_column(String(255), index=True)
    design_json: Mapped[dict] = mapped_column(JSON)

    # Creation provenance.
    created_by_run_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    source_user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )

    # Confirmation provenance.
    confirmed_by_run_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Supersession provenance. Content is never overwritten.
    superseded_by_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("teaching_design_version.id"),
        nullable=True,
        index=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ActiveTeachingDesignRecord(Base):
    """Most recently discussed design version for one conversation."""

    __tablename__ = "active_teaching_design"

    owner_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)

    design_version_id: Mapped[str] = mapped_column(
        ForeignKey("teaching_design_version.id"),
        index=True,
    )
    activated_by_run_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
