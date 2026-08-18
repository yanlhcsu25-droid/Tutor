"""Deterministic conversation curriculum-context service."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import CurriculumNode, Textbook

from .models import ConversationCurriculumContextRecord
from .schemas import CurriculumContextRead, CurriculumContextResolution


def directory_fingerprint(session: Session, textbook_id: str) -> str:
    """Hash stable directory business content, never CurriculumNode UUIDs."""
    textbook = session.get(Textbook, textbook_id)
    if textbook is None:
        raise LookupError("curriculum_textbook_not_found")

    nodes = list(
        session.scalars(
            select(CurriculumNode).where(
                CurriculumNode.textbook_id == textbook_id
            )
        ).all()
    )
    by_id = {node.id: node for node in nodes}
    path_cache: dict[str, tuple[tuple[str, str, str], ...]] = {}

    def logical_path(
        node_id: str,
        visiting: frozenset[str] = frozenset(),
    ) -> tuple[tuple[str, str, str], ...]:
        if node_id in path_cache:
            return path_cache[node_id]
        if node_id in visiting:
            raise ValueError("curriculum_directory_cycle")

        node = by_id.get(node_id)
        if node is None:
            return ()

        segment = (node.node_type, node.code or "", node.title.strip())
        if node.parent_id and node.parent_id in by_id:
            result = (
                *logical_path(node.parent_id, visiting | {node_id}),
                segment,
            )
        else:
            result = (segment,)

        path_cache[node_id] = result
        return result

    payload: list[dict] = []
    for node in nodes:
        parent_path = (
            logical_path(node.parent_id)
            if node.parent_id and node.parent_id in by_id
            else ()
        )
        payload.append(
            {
                "node_type": node.node_type,
                "code": node.code or "",
                "title": node.title.strip(),
                "parent_path": [list(item) for item in parent_path],
                "sort_order": node.sort_order,
            }
        )

    payload.sort(
        key=lambda item: (
            item["sort_order"],
            json.dumps(
                item["parent_path"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            item["node_type"],
            item["code"],
            item["title"],
        )
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def select_curriculum_context(
    session: Session,
    *,
    owner_key: str,
    conversation_id: str,
    textbook_id: str,
    selected_by_run_id: str | None = None,
) -> CurriculumContextRead:
    textbook = session.get(Textbook, textbook_id)
    if textbook is None:
        raise LookupError("curriculum_textbook_not_found")

    current_fingerprint = directory_fingerprint(session, textbook_id)
    record = session.get(
        ConversationCurriculumContextRecord,
        {
            "owner_key": owner_key,
            "conversation_id": conversation_id,
        },
    )
    now = datetime.now(UTC)
    if record is None:
        record = ConversationCurriculumContextRecord(
            owner_key=owner_key,
            conversation_id=conversation_id,
            textbook_id=textbook.id,
            directory_revision=textbook.directory_revision,
            directory_fingerprint=current_fingerprint,
            selected_by_run_id=selected_by_run_id,
            selected_at=now,
        )
        session.add(record)
    else:
        record.textbook_id = textbook.id
        record.directory_revision = textbook.directory_revision
        record.directory_fingerprint = current_fingerprint
        record.selected_by_run_id = selected_by_run_id
        record.selected_at = now

    session.flush()
    return _read_record(
        session,
        record=record,
        textbook=textbook,
        current_fingerprint=current_fingerprint,
    )


def resolve_conversation_curriculum_context(
    session: Session,
    *,
    owner_key: str,
    conversation_id: str,
    selected_by_run_id: str | None = None,
) -> CurriculumContextResolution:
    record = session.get(
        ConversationCurriculumContextRecord,
        {
            "owner_key": owner_key,
            "conversation_id": conversation_id,
        },
    )
    if record is not None:
        textbook = session.get(Textbook, record.textbook_id)
        if textbook is None:
            return CurriculumContextResolution(
                context=None,
                error_code="no_curriculum_context",
            )
        return CurriculumContextResolution(
            context=_read_record(session, record=record, textbook=textbook),
            error_code=None,
        )

    active = list(
        session.scalars(
            select(Textbook)
            .where(Textbook.is_active.is_(True))
            .order_by(Textbook.id)
        ).all()
    )
    if len(active) != 1:
        return CurriculumContextResolution(
            context=None,
            error_code="no_curriculum_context",
        )

    return CurriculumContextResolution(
        context=select_curriculum_context(
            session,
            owner_key=owner_key,
            conversation_id=conversation_id,
            textbook_id=active[0].id,
            selected_by_run_id=selected_by_run_id,
        ),
        error_code=None,
    )


def _read_record(
    session: Session,
    *,
    record: ConversationCurriculumContextRecord,
    textbook: Textbook,
    current_fingerprint: str | None = None,
) -> CurriculumContextRead:
    fingerprint = (
        current_fingerprint
        if current_fingerprint is not None
        else directory_fingerprint(session, textbook.id)
    )

    stale_reasons: list[str] = []
    if record.directory_revision != textbook.directory_revision:
        stale_reasons.append("directory_revision_changed")
    if record.directory_fingerprint != fingerprint:
        stale_reasons.append("directory_fingerprint_changed")

    return CurriculumContextRead(
        owner_key=record.owner_key,
        conversation_id=record.conversation_id,
        textbook_id=textbook.id,
        textbook_name=textbook.name,
        edition=textbook.edition,
        directory_revision=record.directory_revision,
        directory_fingerprint=record.directory_fingerprint,
        selected_by_run_id=record.selected_by_run_id,
        selected_at=record.selected_at,
        current_directory_revision=textbook.directory_revision,
        current_directory_fingerprint=fingerprint,
        stale=bool(stale_reasons),
        stale_reasons=stale_reasons,
    )
