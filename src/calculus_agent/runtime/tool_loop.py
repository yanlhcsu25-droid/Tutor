"""Low-level Tool Loop boundary for the Teacher Agent runtime.

This module owns model/tool interaction primitives.  Policy guards and state
transitions remain supplied by the runtime so this extraction does not alter
Agent behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .observation_projection import project_tool_observation


@dataclass(frozen=True)
class ModelCallResult:
    message: dict
    metadata: dict[str, Any]


def _token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def model_call_metadata(raw: dict, backend: Any) -> dict[str, Any]:
    """Normalize provider token usage without estimating missing values."""
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    provider_usage_raw = usage or {
        key: raw[key]
        for key in ("prompt_eval_count", "eval_count")
        if raw.get(key) is not None
    }
    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage.get("prompt_tokens_details"), dict)
        else {}
    )
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    input_tokens = _token_count(
        usage.get("input_tokens", usage.get("prompt_tokens", raw.get("prompt_eval_count")))
    )
    output_tokens = _token_count(
        usage.get("output_tokens", usage.get("completion_tokens", raw.get("eval_count")))
    )
    total_tokens = _token_count(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    provider_metadata = {
        key: raw[key]
        for key in (
            "id", "created", "system_fingerprint", "finish_reason", "done_reason",
            "total_duration", "load_duration", "prompt_eval_duration", "eval_duration",
        )
        if raw.get(key) is not None
    }
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": _token_count(
            usage.get("cached_tokens", prompt_details.get("cached_tokens"))
        ),
        "reasoning_tokens": _token_count(
            usage.get("reasoning_tokens", completion_details.get("reasoning_tokens"))
        ),
        "actual_usage_available": any(
            value is not None for value in (input_tokens, output_tokens, total_tokens)
        ),
        "provider": raw.get("provider") or getattr(backend, "provider", None),
        "model": raw.get("model") or getattr(backend, "model", None),
        "provider_usage_raw": provider_usage_raw or None,
        "provider_metadata": provider_metadata or None,
    }


class ToolLoop:
    """Execute one model interaction and one validated Tool observation."""

    @staticmethod
    def run_result(
        backend: Any, messages: list[dict], definitions: list[dict]
    ) -> ModelCallResult:
        """Perform one LLM call and keep provider metadata outside the message."""
        raw = backend.complete(messages, definitions)
        if not isinstance(raw, dict):
            raise ValueError("agent_invalid_model_response")
        message = raw.get("message", raw)
        if not isinstance(message, dict):
            raise ValueError("agent_invalid_model_response")
        return ModelCallResult(message=message, metadata=model_call_metadata(raw, backend))

    @staticmethod
    def run(backend: Any, messages: list[dict], definitions: list[dict]) -> dict:
        """Compatibility API: return only the assistant message as before."""
        return ToolLoop.run_result(backend, messages, definitions).message

    @staticmethod
    def parse_call(call: dict) -> tuple[str, dict]:
        function = call.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("agent_invalid_tool_call")
        raw = function.get("arguments", {})
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("agent_invalid_tool_arguments") from exc
        if not isinstance(arguments, dict):
            raise ValueError("agent_invalid_tool_arguments")
        return name, arguments

    @staticmethod
    def execute(toolkit: Any, name: str, arguments: dict) -> Any:
        return toolkit.execute(name, arguments)

    @staticmethod
    def append_observation(
        messages: list[dict],
        *,
        call_id: str,
        name: str,
        payload: dict,
    ) -> None:
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            # Only the LLM-facing copy is projected; the full payload remains
            # available to the runtime and trace recorder.
            "content": json.dumps(
                project_tool_observation(name, payload),
                ensure_ascii=False,
                default=str,
            ),
        })
