"""Agent state persistence infrastructure (Phase 1).

Provides the durable per-conversation workspace pointer and the Agent
lifecycle state.  This layer is intentionally isolated from Agent runtime
behavior and must not import Agent orchestration, tools, or prompts.
"""

from .models import AgentRuntimeState, ConversationWorkspace
from .service import (
    ALLOWED_TRANSITIONS,
    PHASES,
    InvalidStateTransitionError,
    RuntimeStateService,
    WorkspaceService,
)

__all__ = [
    "AgentRuntimeState",
    "ConversationWorkspace",
    "ALLOWED_TRANSITIONS",
    "PHASES",
    "InvalidStateTransitionError",
    "RuntimeStateService",
    "WorkspaceService",
]
