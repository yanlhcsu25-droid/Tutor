"""Tool-call normalization, preparation, execution, and trace shaping."""

from typing import Any
from uuid import uuid4

from calculus_agent.runtime.tool_loop import ToolLoop


def normalize_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Assign stable call IDs while preserving the model's call payload."""
    normalized: list[dict] = []
    for call in tool_calls:
        item = dict(call)
        item["id"] = call.get("id") or f"call_{uuid4().hex}"
        normalized.append(item)
    return normalized


def prepare_tool_call(
    call: dict,
    *,
    addresses: list[Any],
    positions: list[int],
    message: str,
    apply_reference_hints: Any,
    apply_explicit_guards: Any,
) -> tuple[str, str, dict[str, Any]]:
    """Parse a call and apply deterministic, teacher-provenance guards."""
    name, arguments = ToolLoop.parse_call(call)
    arguments = apply_reference_hints(
        tool_name=name, arguments=arguments, addresses=addresses, positions=positions,
    )
    return name, call["id"], apply_explicit_guards(
        tool_name=name, arguments=arguments, message=message,
    )


def execute_tool(toolkit: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Execute at the Toolkit boundary."""
    return ToolLoop.execute(toolkit, name, arguments)


def trace_entry(
    *,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    payload: dict[str, Any],
    observed_version_id: str | None = None,
) -> dict[str, Any]:
    entry = {
        "tool_call_id": call_id,
        "tool_name": name,
        "arguments": arguments,
        "result": payload,
    }
    if name == "read_paper":
        entry["paper_observation"] = {
            "version_id": observed_version_id,
            "positions": arguments.get("positions"),
            "ok": bool(payload.get("ok")),
            "code": payload.get("code"),
        }
    return entry
