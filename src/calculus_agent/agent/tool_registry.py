"""Shared Tool contracts, aggregation, and validated execution boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from .conversation_state import DatabasePendingReplacementStore


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class AgentExecutionContext:
    session: Session
    conversation_id: str | None
    paper_id: str | None
    version_id: str | None
    state_store: DatabasePendingReplacementStore | None
    expected_pending_generation_version: int | None = None
    owner_key: str = "local_teacher"
    run_id: str | None = None
    user_message: str = ""
    observed_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    inspection_state: dict[str, Any] = field(default_factory=dict)
    inspection_call_count: int = 0


@dataclass
class ExecutedTool:
    payload: dict[str, Any]
    status: Literal[
        "completed",
        "needs_clarification",
        "waiting_confirmation",
        "failed",
    ]
    result_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    input_model: type[BaseModel]
    execute: Callable[[BaseModel], ExecutedTool]

    def definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


def build_agent_tools(context: AgentExecutionContext) -> dict[str, AgentTool]:
    """Aggregate domain tool sets; domain implementations live elsewhere."""
    # Lazy imports avoid cycles because TeachingDesign/environment adapters
    # currently import AgentTool/ExecutedTool from this stable boundary.
    from .paper_tool_registry import build_paper_tools
    from .tool_adapters.teaching_environment import build_environment_inspection_tools
    from .tool_adapters.teaching_design import build_teaching_design_tools
    from .tool_adapters.teaching_planning import build_teaching_planning_tools

    tools = dict(build_paper_tools(context))
    for tool in build_environment_inspection_tools(context):
        if tool.name in tools:
            raise ValueError(f"duplicate_tool_name:{tool.name}")
        tools[tool.name] = tool
    for tool in build_teaching_design_tools(context):
        if tool.name in tools:
            raise ValueError(f"duplicate_tool_name:{tool.name}")
        tools[tool.name] = tool
    for tool in build_teaching_planning_tools(context):
        if tool.name in tools:
            raise ValueError(f"duplicate_tool_name:{tool.name}")
        tools[tool.name] = tool
    return tools


def _resolve_schema(schema: dict, root: dict) -> dict:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current: Any = root
    for part in reference[2:].split("/"):
        if not isinstance(current, dict):
            return schema
        current = current.get(part)
    return current if isinstance(current, dict) else schema


def _container_variant(schema: dict, root: dict, expected: type) -> dict | None:
    resolved = _resolve_schema(schema, root)
    variants = [resolved]
    for key in ("anyOf", "oneOf", "allOf"):
        variants.extend(
            _resolve_schema(item, root)
            for item in resolved.get(key, [])
            if isinstance(item, dict)
        )
    expected_type = "object" if expected is dict else "array"
    return next((
        item for item in variants
        if item.get("type") == expected_type
        or (expected is dict and "properties" in item)
    ), None)


def _normalize_schema_value(value: Any, schema: dict, root: dict) -> Any:
    """Decode JSON strings only where the Tool schema expects a container."""
    object_schema = _container_variant(schema, root, dict)
    array_schema = _container_variant(schema, root, list)
    if isinstance(value, str) and (object_schema is not None or array_schema is not None):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            decoded = value
        if isinstance(decoded, dict) and object_schema is not None:
            value = decoded
        elif isinstance(decoded, list) and array_schema is not None:
            value = decoded

    if isinstance(value, dict) and object_schema is not None:
        properties = object_schema.get("properties") or {}
        return {
            key: _normalize_schema_value(item, properties[key], root)
            if key in properties else item
            for key, item in value.items()
        }
    if isinstance(value, list) and array_schema is not None:
        item_schema = array_schema.get("items") or {}
        return [
            _normalize_schema_value(item, item_schema, root)
            for item in value
        ]
    return value


def normalize_tool_arguments(input_model: type[BaseModel], arguments: Any) -> Any:
    """Normalize provider JSON quirks without weakening Pydantic validation."""
    schema = input_model.model_json_schema()
    return _normalize_schema_value(arguments, schema, schema)


def execute_tool(tool: AgentTool, arguments: Any) -> ExecutedTool:
    """Normalize then validate every model-provided argument."""
    normalized = normalize_tool_arguments(tool.input_model, arguments or {})
    try:
        validated = tool.input_model.model_validate(normalized)
    except ValidationError as exc:
        return ExecutedTool(
            payload={
                "ok": False,
                "code": "invalid_tool_arguments",
                "message": "工具参数未通过校验。",
                "details": exc.errors(include_url=False),
            },
            status="failed",
            result_fields={"blocking_errors": ["invalid_tool_arguments"]},
        )
    return tool.execute(validated)
