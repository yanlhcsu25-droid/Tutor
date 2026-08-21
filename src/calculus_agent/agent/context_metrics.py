"""Small, tokenizer-free metrics for model-visible agent context."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
    context_breakdown: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_context(
    *,
    messages: Iterable[dict[str, Any]],
    tool_definitions: Iterable[dict[str, Any]] = (),
    serialized_context: str = "",
    conversation_history: Iterable[dict[str, Any]] = (),
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
    history_messages = list(conversation_history)
    history_chars = sum(
        len(str(message.get("content") or ""))
        for message in history_messages
    )
    tool_schema_chars = sum(
        len(json.dumps(tool, ensure_ascii=False, separators=(",", ":")))
        for tool in tool_definitions
    )
    message_chars = sum(
        len(str(message.get("content") or "")) for message in message_list
    )
    total_chars = message_chars + tool_schema_chars
    workspace_chars = len(serialized_context) * 2
    system_prompt_chars = system_chars
    system_marker = "当前工作区上下文："
    if serialized_context and message_list:
        system_content = next(
            (str(message.get("content") or "") for message in message_list
             if message.get("role") == "system"),
            "",
        )
        suffix = system_marker + serialized_context
        if system_content.endswith(suffix):
            system_prompt_chars = len(system_content) - len(suffix)
    observation_chars = sum(
        len(str(message.get("content") or ""))
        for message in message_list
        if message.get("role") == "tool"
    )
    other_chars = max(
        0,
        message_chars - system_prompt_chars - workspace_chars
        - history_chars - observation_chars,
    )
    breakdown = {
        "system_prompt_chars": system_prompt_chars,
        "tool_schema_chars": tool_schema_chars,
        "conversation_chars": history_chars,
        "workspace_chars": workspace_chars,
        "observation_chars": observation_chars,
        "other_chars": other_chars,
    }
    return ContextMetrics(
        system_chars=system_chars,
        user_chars=user_chars,
        history_chars=history_chars,
        runtime_context_chars=len(serialized_context),
        tool_schema_chars=tool_schema_chars,
        total_chars=total_chars,
        estimated_tokens=(total_chars + 3) // 4,
        context_breakdown=breakdown,
    )
