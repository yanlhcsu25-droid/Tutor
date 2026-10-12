"""Persist and validate one trustworthy question focus across conversation turns."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.agent.schemas import LastFocusedQuestion
from calculus_agent.models import AdjustmentPlanRecord, PaperItem
from calculus_agent.papers.addressing import (
    QuestionAddress,
    resolve_section_item_from_items,
    section_order_map,
)

_TARGETED_CHANGES = {"replace_question", "remove_question", "change_question_score"}


def _items(session: Session, version_id: str) -> list[PaperItem]:
    return list(session.scalars(
        select(PaperItem)
        .where(PaperItem.paper_id == version_id)
        .order_by(PaperItem.position)
    ))


def _focus(
    *,
    paper_id: str,
    version_id: str,
    item: PaperItem,
    source: str,
) -> LastFocusedQuestion:
    return LastFocusedQuestion(
        paper_id=paper_id,
        paper_version_id=version_id,
        question_id=item.question_id,
        position=item.position,
        source=source,
    )


def focus_from_explicit_reference(
    session: Session,
    *,
    paper_id: str,
    version_id: str,
    addresses: Iterable[QuestionAddress],
    positions: Iterable[int],
) -> LastFocusedQuestion | None:
    """Resolve exactly one teacher-explicit target against the current version."""
    addresses = list(addresses)
    positions = list(positions)
    if len(addresses) + len(positions) != 1:
        return None
    items = _items(session, version_id)
    item = (
        resolve_section_item_from_items(
            items,
            section_type=addresses[0].section_type,
            section_order=addresses[0].section_order,
        )
        if addresses
        else next((row for row in items if row.position == positions[0]), None)
    )
    return (
        _focus(
            paper_id=paper_id,
            version_id=version_id,
            item=item,
            source="explicit_user",
        )
        if item is not None else None
    )


def focus_from_read_observation(
    session: Session,
    *,
    paper_id: str,
    version_id: str,
    questions: list[dict],
) -> LastFocusedQuestion | None:
    """Persist a single displayed question after the caller validates read intent."""
    if len(questions) != 1:
        return None
    question = questions[0]
    position = question.get("position")
    question_id = question.get("question_id")
    if not isinstance(position, int) or not isinstance(question_id, str):
        return None
    item = next((row for row in _items(session, version_id) if row.position == position), None)
    if item is None or item.question_id != question_id:
        return None
    return _focus(
        paper_id=paper_id,
        version_id=version_id,
        item=item,
        source="read_paper",
    )


def focus_from_pending_change(
    session: Session,
    *,
    paper_id: str,
    version_id: str,
    plan_id: str | None,
) -> LastFocusedQuestion | None:
    """Recover only a unique, still-current target from a pending preview."""
    if not plan_id:
        return None
    record = session.get(AdjustmentPlanRecord, plan_id)
    if (
        record is None
        or record.status != "pending"
        or record.base_paper_version_id != version_id
    ):
        return None
    targets = {
        (operation.get("position"), operation.get("old_question_id"))
        for operation in (record.operations_json or [])
        if isinstance(operation, dict) and operation.get("type") in _TARGETED_CHANGES
    }
    if len(targets) != 1:
        return None
    position, old_question_id = next(iter(targets))
    if not isinstance(position, int):
        return None
    item = next((row for row in _items(session, version_id) if row.position == position), None)
    if item is None or (old_question_id and item.question_id != old_question_id):
        return None
    return _focus(
        paper_id=paper_id,
        version_id=version_id,
        item=item,
        source="pending_change",
    )


def validated_focus_address(
    session: Session,
    *,
    focus: LastFocusedQuestion | None,
    paper_id: str,
    version_id: str,
) -> QuestionAddress | None:
    """Return an address only when identity, version, position, and question still match."""
    if (
        focus is None
        or focus.paper_id != paper_id
        or focus.paper_version_id != version_id
    ):
        return None
    items = _items(session, version_id)
    item = next((row for row in items if row.position == focus.position), None)
    if item is None or item.question_id != focus.question_id:
        return None
    return QuestionAddress(
        section_type=item.section,
        section_order=section_order_map(items)[item.id],
    )
