"""Teacher Agent runtime orchestration modules."""

from .contracts import AgentStatus, RuntimeErrorInfo, ToolResult
from .runtime import AgentRuntime, UserTurn
from .tool_loop import ToolLoop
from .variants import AgentVariant, PROMPT_ONLY, STATE_POLICY, TOOL_AGENT, get_variant

__all__ = [
    "AgentRuntime", "AgentStatus", "AgentVariant", "PROMPT_ONLY", "RuntimeErrorInfo",
    "STATE_POLICY", "TOOL_AGENT", "ToolLoop", "ToolResult", "UserTurn", "get_variant",
]
