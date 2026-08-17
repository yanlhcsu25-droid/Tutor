"""Deterministic addressing for questions inside one concrete Paper version."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import PaperItem
from calculus_agent.question_types import (
    ALLOWED_QUESTION_TYPES,
    canonical_question_type,
)


class QuestionAddressError(ValueError):
    pass


class QuestionAddress(BaseModel):
    """Teacher-facing section-local question address."""

    model_config = ConfigDict(extra="forbid")

    section_type: str = Field(min_length=1, max_length=40)
    section_order: int = Field(ge=1, le=100)

    @field_validator("section_type", mode="before")
    @classmethod
    def normalize_question_type(cls, value: str) -> str:
        section_type = canonical_question_type(str(value))
        if section_type not in ALLOWED_QUESTION_TYPES:
            raise ValueError("不支持的题型")
        return section_type


def normalize_section_type(value: str) -> str:
    section_type = canonical_question_type(value)
    if section_type not in ALLOWED_QUESTION_TYPES:
        raise QuestionAddressError(
            f"unsupported_question_type:{value}"
        )
    return section_type


def section_order_map(
    items: Iterable[PaperItem],
) -> dict[str, int]:
    """Return ``PaperItem.id -> 1-based order within its section``."""
    counters: dict[str, int] = defaultdict(int)
    result: dict[str, int] = {}

    for item in sorted(items, key=lambda value: value.position):
        section_type = normalize_section_type(item.section)
        counters[section_type] += 1
        result[item.id] = counters[section_type]

    return result


def resolve_section_item_from_items(
    items: Iterable[PaperItem],
    *,
    section_type: str,
    section_order: int,
) -> PaperItem | None:
    """Resolve a teacher-facing section address against already-loaded items."""
    if section_order <= 0:
        return None

    canonical_section = normalize_section_type(section_type)

    section_items = [
        item
        for item in sorted(items, key=lambda value: value.position)
        if normalize_section_type(item.section) == canonical_section
    ]

    index = section_order - 1
    if index >= len(section_items):
        return None

    return section_items[index]


def resolve_section_item(
    session: Session,
    *,
    paper_id: str,
    section_type: str,
    section_order: int,
) -> PaperItem | None:
    """Resolve section-local numbering against the current Paper version."""
    items = list(
        session.scalars(
            select(PaperItem)
            .where(PaperItem.paper_id == paper_id)
            .order_by(PaperItem.position)
        )
    )

    return resolve_section_item_from_items(
        items,
        section_type=section_type,
        section_order=section_order,
    )
