"""Construction of the context sent to the Teacher Agent model.

This module intentionally does not fetch or persist state.  The runtime supplies
all state snapshots and prompt parts; the builder only serializes and assembles
the model-visible messages.
"""

import json
from typing import Any, Iterable

from .context_metrics import ContextMetrics, measure_context


class AgentContextBuilder:
    """Build the initial LLM context for one agent turn."""

    @staticmethod
    def project_workspace(dynamic_context: dict[str, Any]) -> dict[str, Any]:
        """Return the compact, LLM-facing view of runtime workspace state."""
        projected = dict(dynamic_context)

        working_memory = dynamic_context.get("working_memory")
        if isinstance(working_memory, dict):
            active_task = working_memory.get("active_task")
            active_task_view = {}
            if isinstance(active_task, dict):
                for key in ("type", "status", "target_topic", "pending_action"):
                    if key in active_task:
                        active_task_view[key] = active_task[key]
            learning_context = working_memory.get("active_learning_context")
            learning_context_view = None
            if isinstance(learning_context, dict):
                learning_context_view = {
                    key: learning_context.get(key)
                    for key in (
                        "scope_names",
                        "knowledge_names",
                        "learning_need",
                        "generation_diagnosis",
                    )
                    if learning_context.get(key) not in (None, [], {})
                }
            projected["working_memory"] = {"active_task": active_task_view}
            if learning_context_view:
                projected["working_memory"][
                    "active_learning_context"
                ] = learning_context_view
        else:
            projected["working_memory"] = None

        teaching_design = dynamic_context.get("active_teaching_design")
        if isinstance(teaching_design, dict):
            content = teaching_design.get("content")
            content = content if isinstance(content, dict) else {}
            knowledge_plan = content.get("knowledge_plan") or []
            key_knowledge = []
            for item in knowledge_plan:
                if isinstance(item, dict) and item.get("name"):
                    key_knowledge.append({
                        "name": item["name"],
                        "role": item.get("role"),
                        "priority": item.get("priority"),
                    })
            projected["active_teaching_design"] = {
                "version_id": teaching_design.get("version_id"),
                "status": teaching_design.get("status"),
                "scope": content.get("scope_names", []),
                "goal": content.get("objective"),
                "key_knowledge": key_knowledge,
            }
        else:
            projected["active_teaching_design"] = None
        return projected

    @classmethod
    def serialize_workspace(
        cls,
        dynamic_context: dict[str, Any],
        **_: Any,
    ) -> str:
        """Serialize only the LLM-facing projection (never mutate input state)."""
        return json.dumps(cls.project_workspace(dynamic_context), ensure_ascii=False)

    def build(
        self,
        *,
        message: str,
        recent_messages: Iterable[dict[str, Any]],
        dynamic_context: dict[str, Any],
        system_parts: Iterable[str],
        tool_definitions: Iterable[dict[str, Any]] = (),
    ) -> tuple[list[dict[str, Any]], str, ContextMetrics]:
        """Return ``(messages, serialized_context, metrics)`` without changing inputs."""
        serialized_context = self.serialize_workspace(dynamic_context)
        projected_context = self.project_workspace(dynamic_context)
        history_messages = list(recent_messages)
        assembled_system_parts = [*system_parts, "当前工作区上下文：" + serialized_context]
        system_content = "\n\n".join(assembled_system_parts)
        current_user_content = (
            message
            + "\n\n<current_workspace_state>"
            + serialized_context
            + "</current_workspace_state>"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
            *history_messages,
            {"role": "user", "content": current_user_content},
        ]
        metrics = measure_context(
            messages=messages,
            tool_definitions=tool_definitions,
            serialized_context=serialized_context,
            conversation_history=history_messages,
            workspace_context=projected_context,
        )
        return messages, serialized_context, metrics
