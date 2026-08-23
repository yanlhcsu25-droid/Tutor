"""Small trace DTOs shared by legacy-format evaluation adapters."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TraceEntry:
    step: int
    actor: str
    tool_name: str
    arguments: dict
    result: dict
    status: str
    duration_ms: int
