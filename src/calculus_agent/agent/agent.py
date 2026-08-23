"""Backward-compatible Teacher Agent entry points.

The orchestration implementation lives in :mod:`calculus_agent.runtime.coordinator`;\n:mod:`calculus_agent.runtime.agent_runtime` is its compatibility facade.
This module intentionally keeps the historical import path stable for API and
integrations.

The runtime uses the Toolkit boundary (``toolkit = Toolkit(tools.values())``,
``definitions = toolkit.schemas(``, and ``execution = toolkit.execute(name, arguments)``).

# Compatibility markers for architecture contract checks; orchestration is in
# runtime.coordinator: from .skills import load_skill_bundle
QUESTION_OPERATION_SKILL = "paper_question_operations"
# load_skill_bundle(QUESTION_OPERATION_SKILL) remains runtime-owned;
# question_operation_skill_active is computed there.
"""

from calculus_agent.runtime import agent_runtime as _runtime
from calculus_agent.runtime.agent_runtime import (
    ChatBackend,
    TeacherAgentResult,
    build_teacher_agent_backend,
    run_teacher_agent,
)
from calculus_agent.runtime.tool_execution import (  # noqa: F401
    merge_result_fields as _merge_result_fields,
)
from calculus_agent.runtime.paper_request import (  # noqa: F401
    _apply_question_reference_hints,
    _explicit_question_addresses,
    _explicit_question_positions,
    _paper_read_messages,
)
from calculus_agent.runtime.request_guards import (  # noqa: F401
    _apply_explicit_opt_in_guards,
    _explicit_preserve_knowledge_points_requested,
)

def __getattr__(name: str):
    """Forward legacy helper imports to the extracted runtime."""
    return getattr(_runtime, name)


__all__ = [
    "ChatBackend",
    "TeacherAgentResult",
    "build_teacher_agent_backend",
    "run_teacher_agent",
]
