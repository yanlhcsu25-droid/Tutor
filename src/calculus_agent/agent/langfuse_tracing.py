"""Optional Langfuse observability layer for the Teacher Agent.

This module is **additive only** — it never affects Teacher Agent business
behaviour. When Langfuse is unavailable, mis-configured, or the SDK raises
during initialization, every helper degrades to a silent no-op.

Callers always receive ``span | None`` from each contextmanager; they must
guard their ``span.update(...)`` calls accordingly. Langfuse failures never
propagate out of this module.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from .trace_log import redact_trace_value

logger = logging.getLogger(__name__)

# Lazy single-client cache: we pay for get_client() at most once per process.
_CLIENT_CACHE: dict[str, Any] = {"client": None}


def safe_get_client() -> Any | None:
    """Return the Langfuse client, or ``None`` when unavailable. Never raises."""
    if _CLIENT_CACHE["client"] is not None:
        return _CLIENT_CACHE["client"]
    try:
        from langfuse import get_client  # type: ignore
        client = get_client()
    except Exception:
        return None
    _CLIENT_CACHE["client"] = client
    return client


def _safe_cm_exit(cm: Any) -> None:
    if cm is None:
        return
    try:
        cm.__exit__(None, None, None)
    except Exception:
        pass


@contextmanager
def teacher_turn_span(
    conversation_id: str | None, user_message: str
) -> Iterator[Any | None]:
    """Wrap one Agent turn as a Langfuse agent observation.

    Yields the active span (or ``None`` when Langfuse is unavailable). The
    caller must call ``span.update(output=...)`` once the final result is
    known. Child observations opened inside this block automatically inherit
    ``session_id=conversation_id``.
    """
    client = safe_get_client()
    if client is None:
        yield None
        return

    try:
        span_cm = client.start_as_current_observation(
            as_type="agent",
            name="teacher-agent-turn",
            input={
                "conversation_id": conversation_id,
                "user_message": redact_trace_value(user_message),
            },
        )
        span = span_cm.__enter__()
    except Exception:
        yield None
        return

    propagate_cm: Any | None = None
    try:
        from langfuse import propagate_attributes  # type: ignore
        propagate_cm = propagate_attributes(session_id=conversation_id)
        propagate_cm.__enter__()
    except Exception:
        propagate_cm = None

    try:
        yield span
    finally:
        if propagate_cm is not None:
            _safe_cm_exit(propagate_cm)
        _safe_cm_exit(span_cm)


@contextmanager
def llm_generation_span(
    backend: Any, messages: list, tools_definitions: list
) -> Iterator[Any | None]:
    """Wrap one ``backend.complete`` call as a Langfuse generation observation."""
    client = safe_get_client()
    if client is None:
        yield None
        return

    model = getattr(backend, "model", None)
    kwargs: dict[str, Any] = {
        "as_type": "generation",
        "name": "teacher-agent-llm",
        "input": {
            "messages": redact_trace_value(messages),
            "tools": redact_trace_value(tools_definitions),
        },
    }
    if model:
        kwargs["model"] = model

    try:
        span_cm = client.start_as_current_observation(**kwargs)
        span = span_cm.__enter__()
    except Exception:
        yield None
        return

    try:
        yield span
    finally:
        _safe_cm_exit(span_cm)


@contextmanager
def tool_observation_span(tool_name: str, arguments: Any) -> Iterator[Any | None]:
    """Wrap one ``execute_tool`` call as a Langfuse tool observation."""
    client = safe_get_client()
    if client is None:
        yield None
        return

    try:
        span_cm = client.start_as_current_observation(
            as_type="tool",
            name=tool_name,
            input={"arguments": redact_trace_value(arguments)},
        )
        span = span_cm.__enter__()
    except Exception:
        yield None
        return

    try:
        yield span
    finally:
        _safe_cm_exit(span_cm)


def safe_update(span: Any, **kwargs: Any) -> None:
    """Call ``span.update(...)`` swallowing any Langfuse failure. No-op if span is None."""
    if span is None:
        return
    try:
        span.update(**kwargs)
    except Exception:
        pass
