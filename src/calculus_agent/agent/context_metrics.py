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
    workspace_breakdown: dict[str, int] = field(default_factory=dict)
    workspace_detail_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_context(
    *,
    messages: Iterable[dict[str, Any]],
    tool_definitions: Iterable[dict[str, Any]] = (),
    serialized_context: str = "",
    conversation_history: Iterable[dict[str, Any]] = (),
    workspace_context: dict[str, Any] | None = None,
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
    workspace_breakdown = _measure_workspace_breakdown(
        workspace_context,
        workspace_chars=workspace_chars,
    )
    return ContextMetrics(
        system_chars=system_chars,
        user_chars=user_chars,
        history_chars=history_chars,
        runtime_context_chars=len(serialized_context),
        tool_schema_chars=tool_schema_chars,
        total_chars=total_chars,
        estimated_tokens=(total_chars + 3) // 4,
        context_breakdown=breakdown,
        workspace_breakdown=workspace_breakdown,
        workspace_detail_breakdown=_measure_workspace_detail_breakdown(
            workspace_context,
        ),
    )


def _measure_workspace_detail_breakdown(
    workspace_context: dict[str, Any] | None,
) -> dict[str, dict[str, int]]:
    if not isinstance(workspace_context, dict):
        return {"active_task": {}, "teaching_design": {}}

    def chars(value: Any) -> int:
        if value is None:
            return 0
        return len(json.dumps(value, ensure_ascii=False)) * 2

    memory = workspace_context.get("working_memory")
    memory = memory if isinstance(memory, dict) else {}
    active_task = memory.get("active_task")
    active_task = active_task if isinstance(active_task, dict) else {}
    design = workspace_context.get("active_teaching_design")
    design = design if isinstance(design, dict) else {}
    content = design.get("content")
    content = content if isinstance(content, dict) else {}
    constraints = content.get("assessment_plan")
    provenance = content.get("evidence_refs")
    core_content = {
        key: value
        for key, value in content.items()
        if key not in {"assessment_plan", "evidence_refs"}
    }
    metadata = {
        key: value
        for key, value in design.items()
        if key != "content"
    }
    return {
        "active_task": {
            **{str(key): chars(value) for key, value in active_task.items()},
        },
        "teaching_design": {
            "content_chars": chars(core_content),
            "metadata_chars": chars(metadata),
            "constraints_chars": chars(constraints),
            "provenance_chars": chars(provenance),
        },
    }


def _measure_workspace_breakdown(
    workspace_context: dict[str, Any] | None,
    *,
    workspace_chars: int,
) -> dict[str, int]:
    """Attribute serialized workspace values without changing the payload."""
    if not isinstance(workspace_context, dict):
        return {
            "active_task_chars": 0,
            "teaching_design_chars": 0,
            "generation_chars": 0,
            "paper_chars": 0,
            "memory_chars": 0,
            "other_chars": workspace_chars,
        }

    def chars(value: Any) -> int:
        if value is None:
            return 0
        return len(json.dumps(value, ensure_ascii=False)) * 2

    memory = workspace_context.get("working_memory")
    memory_dict = memory if isinstance(memory, dict) else {}
    active_task = memory_dict.get("active_task")
    generation_values = {
        "pending": workspace_context.get("pending"),
        "generation_summary": memory_dict.get("generation_summary"),
    }
    generation = (
        generation_values
        if any(value is not None for value in generation_values.values())
        else None
    )
    paper = workspace_context.get("current_paper")
    teaching_design = workspace_context.get("active_teaching_design")
    memory_rest = {
        key: value
        for key, value in memory_dict.items()
        if key not in {"active_task", "generation_summary"}
    }
    known = {
        "active_task_chars": chars(active_task),
        "teaching_design_chars": chars(teaching_design),
        "generation_chars": chars(generation),
        "paper_chars": chars(paper),
        "memory_chars": chars(memory_rest),
    }
    known_total = sum(known.values())
    known["other_chars"] = max(0, workspace_chars - known_total)
    return known
