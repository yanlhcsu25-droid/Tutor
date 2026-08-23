"""Canonical result and error contracts for the Agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AgentStatus = Literal[
    "completed",
    "needs_clarification",
    "waiting_confirmation",
    "failed",
]


@dataclass(frozen=True)
class ToolResult:
    """The only result shape accepted at the Tool/runtime boundary."""

    payload: dict[str, Any]
    status: AgentStatus
    result_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        status: AgentStatus = "failed",
        details: Any = None,
        result_fields: dict[str, Any] | None = None,
    ) -> "ToolResult":
        payload: dict[str, Any] = {"ok": False, "code": code, "message": message}
        if details is not None:
            payload["details"] = details
        return cls(
            payload=payload,
            status=status,
            result_fields=result_fields or {"blocking_errors": [code]},
        )


@dataclass(frozen=True)
class RuntimeErrorInfo:
    """Serializable error shape shared by traces, finalization, and reports."""

    error_code: str
    error_type: str
    error_message: str
    error_stage: str

    @classmethod
    def from_exception(cls, exc: Exception, *, stage: str) -> "RuntimeErrorInfo":
        message = str(exc)
        return cls(
            error_code=(message if message.startswith("agent_") else "agent_execution_failed"),
            error_type=type(exc).__name__,
            error_message=message,
            error_stage=stage,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_stage": self.error_stage,
        }
