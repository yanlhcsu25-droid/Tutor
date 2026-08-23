"""Backward-compatible Runtime facade.

The turn coordinator lives in :mod:`calculus_agent.runtime.coordinator`.  Keep
this module as the stable import path for API clients and historical tests.
"""

from . import coordinator as _coordinator


def __getattr__(name: str):
    return getattr(_coordinator, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_coordinator)))


# Explicit public entry points improve static imports while private compatibility
# helpers continue to resolve through ``__getattr__``.
run_teacher_agent = _coordinator.run_teacher_agent
build_teacher_agent_backend = _coordinator.build_teacher_agent_backend
ChatBackend = _coordinator.ChatBackend
TeacherAgentResult = _coordinator.TeacherAgentResult
