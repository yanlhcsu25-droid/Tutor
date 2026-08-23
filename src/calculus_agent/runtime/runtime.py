"""Public, readable entry point for one Teacher Agent turn.

The runtime pipeline is intentionally explicit:

UserTurn -> routing/context -> model decision -> ToolExecutor -> state transition
         -> FinalizationPolicy -> trace persistence.

Business tools own domain mutation.  The model may propose Tool calls, but it
cannot commit lifecycle state or approve its own final response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from calculus_agent.runtime.variants import AgentVariant, STATE_POLICY


@dataclass(frozen=True)
class UserTurn:
    message: str
    conversation_id: str | None = None
    owner_key: str | None = None
    paper_id: str | None = None
    version_id: str | None = None
    operation_id: str | None = None


class AgentRuntime:
    """Stable public interface for executing exactly one user turn.

    The coordinator injected here owns orchestration only. Context projection,
    model execution, Tool execution, policies, and domain services remain
    independently testable boundaries.
    """

    def __init__(
        self,
        session: Session,
        *,
        coordinator: Callable[..., Any],
        backend: Any = None,
        state_store: Any = None,
        max_tool_rounds: int = 8,
        trace_recorder: Any = None,
        default_owner_key: str,
        variant: AgentVariant = STATE_POLICY,
        tool_fault_injector: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.session = session
        self._coordinator = coordinator
        self.backend = backend
        self.state_store = state_store
        self.max_tool_rounds = max_tool_rounds
        self.trace_recorder = trace_recorder
        self.default_owner_key = default_owner_key
        self.variant = variant
        self.tool_fault_injector = tool_fault_injector

    def run(self, turn: UserTurn) -> Any:
        """Execute the deterministic model/Tool pipeline for ``turn``."""
        return self._coordinator(
            self.session,
            turn.message,
            conversation_id=turn.conversation_id,
            owner_key=turn.owner_key or self.default_owner_key,
            paper_id=turn.paper_id,
            version_id=turn.version_id,
            state_store=self.state_store,
            backend=self.backend,
            max_tool_rounds=self.max_tool_rounds,
            trace_recorder=self.trace_recorder,
            variant=self.variant,
            tool_fault_injector=self.tool_fault_injector,
            operation_id=turn.operation_id,
        )
