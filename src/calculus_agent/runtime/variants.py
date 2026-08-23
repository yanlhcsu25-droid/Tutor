"""Policy configuration for reliability benchmark variants."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentVariant:
    name: str = "state-policy"
    tools_enabled: bool = True
    persistent_state: bool = True
    grounding_guard: bool = True
    confirmation_guard: bool = True
    recovery_policy: bool = True


STATE_POLICY = AgentVariant()
TOOL_AGENT = AgentVariant(
    name="tool-agent",
    grounding_guard=False,
    confirmation_guard=False,
    recovery_policy=False,
)
PROMPT_ONLY = AgentVariant(
    name="prompt-only",
    tools_enabled=False,
    persistent_state=False,
    grounding_guard=False,
    confirmation_guard=False,
    recovery_policy=False,
)

VARIANTS = {item.name: item for item in (STATE_POLICY, TOOL_AGENT, PROMPT_ONLY)}


def get_variant(name: str) -> AgentVariant:
    try:
        return VARIANTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown_agent_variant:{name}") from exc
