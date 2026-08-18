"""Compatibility Toolkit boundary for the Teacher Agent.

Phase 4A centralizes model-visible schema exposure and validated execution
without moving existing deterministic business handlers. Phase 4B will move
those handlers out of tool_registry.py into domain services.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .tool_registry import AgentTool, ExecutedTool, execute_tool


ToolDefinitionTransform = Callable[[AgentTool], dict]


class Toolkit:
    """Register, expose and execute model-callable tools through one boundary."""

    def __init__(self, tools: Iterable[AgentTool] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        self._groups: dict[str, set[str]] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool, *, group: str = "basic") -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate_tool_name:{tool.name}")
        self._tools[tool.name] = tool
        self._groups.setdefault(group, set()).add(tool.name)

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def names(self, *, groups: set[str] | None = None) -> list[str]:
        if groups is None:
            return list(self._tools)

        allowed: set[str] = set()
        for group in groups:
            allowed.update(self._groups.get(group, set()))
        return [name for name in self._tools if name in allowed]

    def schema(
        self,
        name: str,
        *,
        transform: ToolDefinitionTransform | None = None,
    ) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown_tool:{name}")
        return transform(tool) if transform else tool.definition()

    def schemas(
        self,
        *,
        names: list[str] | None = None,
        groups: set[str] | None = None,
        transform: ToolDefinitionTransform | None = None,
    ) -> list[dict]:
        selected = names if names is not None else self.names(groups=groups)
        return [self.schema(name, transform=transform) for name in selected]

    def execute(self, name: str, arguments: Any) -> ExecutedTool:
        tool = self._tools.get(name)
        if tool is None:
            return ExecutedTool(
                payload={
                    "ok": False,
                    "code": "unknown_tool",
                    "message": f"不存在工具：{name}",
                },
                status="failed",
                result_fields={"blocking_errors": ["unknown_tool"]},
            )
        return execute_tool(tool, arguments)
