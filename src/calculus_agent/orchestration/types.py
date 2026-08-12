from dataclasses import dataclass, field
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from calculus_agent.schemas import PaperPreviewRead


class ChatBackend(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> dict: ...


ToolHandler = Callable[[dict], dict]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict
    handler: ToolHandler

    def chat_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def ollama_schema(self) -> dict:
        """Backward-compatible alias for existing integrations."""
        return self.chat_schema()


@dataclass
class TraceEntry:
    step: int
    actor: str
    tool_name: str
    arguments: dict
    result: dict
    status: str
    duration_ms: int


@dataclass
class RunBudget:
    max_steps: int = 12
    max_delegations: int = 4
    steps_used: int = 0
    delegations_used: int = 0
    repeated_calls: dict[str, int] = field(default_factory=dict)

    def consume_tool(self, signature: str) -> None:
        if self.steps_used >= self.max_steps:
            raise RuntimeError(f"Agent step budget exceeded ({self.max_steps})")
        repeated = self.repeated_calls.get(signature, 0) + 1
        self.repeated_calls[signature] = repeated
        if repeated > 2:
            raise RuntimeError("Repeated identical tool call blocked")
        self.steps_used += 1

    def consume_delegation(self) -> None:
        if self.delegations_used >= self.max_delegations:
            raise RuntimeError(f"Delegation budget exceeded ({self.max_delegations})")
        self.delegations_used += 1


@dataclass
class AgentRunContext:
    session: Session
    budget: RunBudget
    traces: list[TraceEntry] = field(default_factory=list)
    current_paper: PaperPreviewRead | None = None


@dataclass
class AgentResult:
    text: str
    status: str
