"""Basic CRUD services for the Agent state layer (Phase 1).

This module only establishes the persistence boundary.  It deliberately does
NOT wire into Agent behavior, WorkingMemory, Generation, or tool routing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .models import AgentRuntimeState, ConversationWorkspace


PHASES: tuple[str, ...] = (
    "idle",
    "planning",
    "waiting",
    "executing",
    "completed",
    "failed",
)

# Allowed transitions.  Two "lanes" are supported:
#
#   fine-grained: idle -> planning -> waiting -> executing -> completed/failed
#   fast-lane:    idle -> waiting -> completed   (used by generation, where the
#                 planning/executing windows are not observable)
#
# ``waiting -> idle`` is the discard/cancel reset, and ``completed``/``failed``
# may restart into ``planning``/``waiting`` for a new task / retry.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "idle": frozenset({"planning", "waiting"}),
    "planning": frozenset({"waiting", "failed"}),
    "waiting": frozenset({"executing", "completed", "failed", "idle"}),
    "executing": frozenset({"completed", "failed"}),
    "completed": frozenset({"planning", "waiting"}),
    "failed": frozenset({"planning", "waiting"}),
}


class InvalidStateTransitionError(RuntimeError):
    """Raised when a phase transition is not in :data:`ALLOWED_TRANSITIONS`."""

    def __init__(self, from_phase: str, to_phase: str) -> None:
        super().__init__(f"invalid_state_transition: {from_phase} -> {to_phase}")
        self.from_phase = from_phase
        self.to_phase = to_phase


class WorkspaceService:
    """CRUD for the per-conversation object-pointer workspace."""

    _UPDATABLE_FIELDS = frozenset(
        {
            "active_type",
            "current_paper_id",
            "current_version_id",
            "pending_generation_id",
        }
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, conversation_id: str) -> ConversationWorkspace:
        workspace = self.session.get(ConversationWorkspace, conversation_id)
        if workspace is None:
            workspace = ConversationWorkspace(conversation_id=conversation_id)
            self.session.add(workspace)
            self.session.flush()
        return workspace

    def get(self, conversation_id: str) -> ConversationWorkspace | None:
        return self.session.get(ConversationWorkspace, conversation_id)

    def update(
        self,
        conversation_id: str,
        fields: dict,
    ) -> ConversationWorkspace:
        """Apply a partial pointer update.  Unknown keys raise ``ValueError``."""
        unknown = set(fields) - self._UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown_workspace_fields: {sorted(unknown)}")

        workspace = self.get_or_create(conversation_id)
        changed = False
        for name, value in fields.items():
            if getattr(workspace, name) != value:
                setattr(workspace, name, value)
                changed = True
        if changed:
            workspace.updated_at = datetime.now(UTC)
        self.session.flush()
        return workspace


class RuntimeStateService:
    """CRUD + minimal phase transition for the Agent lifecycle state."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, conversation_id: str) -> AgentRuntimeState:
        state = self.session.get(AgentRuntimeState, conversation_id)
        if state is None:
            state = AgentRuntimeState(
                conversation_id=conversation_id,
                phase="idle",
                revision=0,
            )
            self.session.add(state)
            self.session.flush()
        return state

    def get(self, conversation_id: str) -> AgentRuntimeState | None:
        return self.session.get(AgentRuntimeState, conversation_id)

    def transition(
        self,
        conversation_id: str,
        phase: str,
        task_type: str | None = None,
        waiting_for: str | None = None,
    ) -> AgentRuntimeState:
        """Move the state to ``phase`` if the transition is allowed.

        An invalid transition raises :class:`InvalidStateTransitionError`.
        ``revision`` is incremented on every successful *phase change*.
        Transitioning to the current phase is an idempotent no-op that only
        refreshes ``task_type`` / ``waiting_for`` when provided.
        """
        state = self.get_or_create(conversation_id)
        if phase == state.phase:
            if task_type is not None:
                state.task_type = task_type
            if waiting_for is not None:
                state.waiting_for = waiting_for
            self.session.flush()
            return state
        if phase not in ALLOWED_TRANSITIONS.get(state.phase, frozenset()):
            raise InvalidStateTransitionError(state.phase, phase)

        state.phase = phase
        if task_type is not None:
            state.task_type = task_type
        if waiting_for is not None:
            state.waiting_for = waiting_for
        state.revision += 1
        state.updated_at = datetime.now(UTC)
        self.session.flush()
        return state
