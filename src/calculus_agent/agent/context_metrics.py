"""Small, tokenizer-free metrics for model-visible agent context."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ContextMetrics:
    system_chars: int
    user_chars: int
    history_chars: int
    runtime_context_chars: int
    tool_schema_chars: int
    total_chars: int
    estimated_tokens: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def measure_context(
    *,
    messages: Iterable[dict[str, Any]],
    tool_definitions: Iterable[dict[str, Any]] = (),
    serialized_context: str = "",
) -> ContextMetrics:
    """Measure the supplied payload without modifying it.

    ``total_chars`` is the size of the actual model payload (message content
    plus tool schemas).  ``runtime_context_chars`` is reported separately
    because runtime context is intentionally present in both system and user
    messages in the current prompt contract.
    """
    message_list = list(messages)
    system_chars = sum(
        len(str(message.get("content") or ""))
        for message in message_list
        if message.get("role") == "system"
    )
    user_chars = sum(
        len(str(message.get("content") or ""))
        for message in message_list
        if message.get("role") == "user"
    )
    history_chars = sum(
        len(str(message.get("content") or ""))
        for message in message_list
        if message.get("role") not in {"system", "user"}
    )
    tool_schema_chars = sum(
        len(json.dumps(tool, ensure_ascii=False, separators=(",", ":")))
        for tool in tool_definitions
    )
    total_chars = sum(
        len(str(message.get("content") or "")) for message in message_list
    ) + tool_schema_chars
    return ContextMetrics(
        system_chars=system_chars,
        user_chars=user_chars,
        history_chars=history_chars,
        runtime_context_chars=len(serialized_context),
        tool_schema_chars=tool_schema_chars,
        total_chars=total_chars,
        estimated_tokens=(total_chars + 3) // 4,
    )
