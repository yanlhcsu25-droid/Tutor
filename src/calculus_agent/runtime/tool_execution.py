"""Tool-call normalization, preparation, execution, and trace shaping."""

import json
from typing import Any
from uuid import uuid4

from calculus_agent.runtime.contracts import ToolResult
from calculus_agent.runtime.tool_loop import ToolLoop


_MUTATING_TOOLS = frozenset({
    "prepare_generation_plan", "confirm_generation", "discard_pending_plan",
    "preview_paper_changes", "confirm_paper_changes", "operate_paper_version",
    "create_teaching_design", "revise_teaching_design", "confirm_teaching_design",
    "discard_teaching_design", "activate_teaching_design",
})


class ToolExecutor:
    """Validate Tool results and isolate every mutation behind a savepoint."""

    def __init__(
        self,
        toolkit: Any,
        *,
        session: Any = None,
        fault_injector: Any = None,
    ) -> None:
        self.toolkit = toolkit
        self.session = session
        self.fault_injector = fault_injector
        self._successful_mutations: dict[str, ToolResult] = {}

    def prepare(
        self,
        call: dict,
        *,
        addresses: list[Any],
        positions: list[int],
        message: str,
        apply_reference_hints: Any,
        apply_explicit_guards: Any,
    ) -> tuple[str, str, dict[str, Any]]:
        return prepare_tool_call(
            call,
            addresses=addresses,
            positions=positions,
            message=message,
            apply_reference_hints=apply_reference_hints,
            apply_explicit_guards=apply_explicit_guards,
        )

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        fingerprint = json.dumps(
            [name, arguments], ensure_ascii=False, sort_keys=True, default=str,
        )
        if name in _MUTATING_TOOLS and fingerprint in self._successful_mutations:
            return self._successful_mutations[fingerprint]

        def run() -> ToolResult:
            result = (
                self.fault_injector(name, arguments)
                if self.fault_injector is not None
                else execute_tool(self.toolkit, name, arguments)
            )
            if not isinstance(result, ToolResult):
                raise TypeError("agent_invalid_tool_result")
            return result

        if self.session is None:
            result = run()
        else:
            # Any exception rolls back partial writes before it crosses the
            # Tool boundary. Successful results remain in the outer turn transaction.
            with self.session.begin_nested():
                result = run()

        if name in _MUTATING_TOOLS and result.payload.get("ok") is True:
            self._successful_mutations[fingerprint] = result
        return result


def exposed_tool_names(definitions: list[dict[str, Any]]) -> frozenset[str]:
    """Return the executable capability set for the current model round."""
    return frozenset(
        name
        for definition in definitions
        if isinstance(definition, dict)
        for name in [(definition.get("function") or {}).get("name")]
        if isinstance(name, str) and name
    )


def merge_result_fields(target: dict[str, Any], values: dict[str, Any]) -> None:
    """Merge Tool result fields without duplicating list-valued diagnostics."""
    for key, value in values.items():
        if key in {"warnings", "blocking_errors", "clarification_questions"}:
            existing = target.setdefault(key, [])
            existing.extend(item for item in value if item not in existing)
        elif value is not None:
            target[key] = value


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


def execute_tool(toolkit: Any, name: str, arguments: dict[str, Any]) -> ToolResult:
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
