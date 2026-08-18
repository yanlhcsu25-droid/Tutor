"""Structured read models for curriculum context."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CurriculumContextSnapshot(BaseModel):
    textbook_id: str
    textbook_name: str
    edition: str | None = None
    directory_revision: int
    directory_fingerprint: str


class CurriculumContextRead(CurriculumContextSnapshot):
    owner_key: str
    conversation_id: str
    selected_by_run_id: str | None = None
    selected_at: datetime
    current_directory_revision: int
    current_directory_fingerprint: str
    stale: bool
    stale_reasons: list[str]


class CurriculumContextResolution(BaseModel):
    context: CurriculumContextRead | None = None
    error_code: Literal["no_curriculum_context"] | None = None
