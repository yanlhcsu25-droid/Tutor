"""Explicit reconciliation for legacy RuntimeState / domain-state drift.

Runtime phase is a control boundary for new writes.  This module is the only
place where persisted domain state may repair an older lifecycle projection.
"""

from __future__ import annotations

from .service import InvalidStateTransitionError, RuntimeStateService


class StateReconciliationError(RuntimeError):
    """Domain state and RuntimeState cannot be safely brought into agreement."""


def reconcile_pending_generation(
    service: RuntimeStateService,
    conversation_id: str,
) -> None:
    """Restore the waiting generation phase for a persisted legacy pending plan.

    Old records can have a PendingGeneration without a RuntimeState row (or
    with idle/planning/completed/failed).  These states have no active write in
    progress and can safely be restored to ``waiting``.  ``executing`` is not
    repaired: it may represent an in-flight write and requires explicit retry
    or operator intervention rather than silently reopening confirmation.
    """
    state = service.get_or_create(conversation_id)
    if state.phase == "waiting":
        service.transition(
            conversation_id, "waiting", task_type="generation",
            waiting_for="teacher_confirmation",
        )
        return
    if state.phase == "executing":
        raise StateReconciliationError(
            "generation_state_conflict: pending_generation while runtime is executing"
        )
    try:
        service.transition(
            conversation_id, "waiting", task_type="generation",
            waiting_for="teacher_confirmation",
        )
    except InvalidStateTransitionError as error:
        raise StateReconciliationError(
            f"generation_state_reconciliation_failed: {error}"
        ) from error
