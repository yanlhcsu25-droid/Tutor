import json
import time

from calculus_agent.orchestration.types import (
    AgentResult,
    AgentRunContext,
    AgentTool,
    ChatBackend,
    TraceEntry,
)


class ToolAgent:
    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        backend: ChatBackend,
        tools: list[AgentTool],
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.backend = backend
        self.tools = {tool.name: tool for tool in tools}

    def run(self, prompt: str, context: AgentRunContext) -> AgentResult:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        schemas = [tool.chat_schema() for tool in self.tools.values()]
        while True:
            response = self.backend.complete(messages, schemas)
            message = response.get("message") or {}
            messages.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return AgentResult(text=str(message.get("content") or ""), status="completed")
            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = _arguments(function.get("arguments"))
                signature = json.dumps(
                    [self.name, name, arguments], ensure_ascii=False, sort_keys=True
                )
                started = time.perf_counter()
                status = "success"
                trace: TraceEntry | None = None
                try:
                    context.budget.consume_tool(signature)
                    trace = TraceEntry(
                        step=context.budget.steps_used,
                        actor=self.name,
                        tool_name=name,
                        arguments=arguments,
                        result={},
                        status="running",
                        duration_ms=0,
                    )
                    context.traces.append(trace)
                    tool = self.tools.get(name)
                    if tool is None:
                        raise ValueError(f"Tool not allowed for {self.name}: {name}")
                    result = tool.handler(arguments)
                except Exception as error:
                    status = "error"
                    result = {"error": str(error)}
                    if trace is None:
                        trace = TraceEntry(
                            step=context.budget.steps_used,
                            actor=self.name,
                            tool_name=name,
                            arguments=arguments,
                            result={},
                            status="running",
                            duration_ms=0,
                        )
                        context.traces.append(trace)
                duration_ms = round((time.perf_counter() - started) * 1000)
                trace.result = result
                trace.status = status
                trace.duration_ms = duration_ms
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or name),
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )


def _arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
