"""Structured runtime-state projection used by Agent context and tracing."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from calculus_agent.teaching_design.service import TeachingDesignService


def active_teaching_design_snapshot(
    session: Session,
    *,
    owner_key: str,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    try:
        design = TeachingDesignService(session).get_active(
            owner_key=owner_key,
            conversation_id=conversation_id,
        )
    except Exception:
        return None
    return design.model_dump(mode="json") if design is not None else None


def build_runtime_state_snapshot(
    session: Session,
    *,
    store: Any,
    owner_key: str,
    conversation_id: str | None,
) -> dict[str, Any]:
    """Return observable state without turning chat history into business truth."""
    working_memory = None
    if store is not None and conversation_id and hasattr(store, "get_memory"):
        try:
            working_memory = store.get_memory(conversation_id).model_dump(
                mode="json"
            )
        except Exception:
            working_memory = None

    return {
        "working_memory": working_memory,
        "active_teaching_design": active_teaching_design_snapshot(
            session,
            owner_key=owner_key,
            conversation_id=conversation_id,
        ),
    }
