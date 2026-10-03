"""Model-call execution and observability for a prepared model turn."""

from datetime import UTC, datetime
from typing import Any

from calculus_agent.agent.langfuse_tracing import llm_generation_span, safe_update
from calculus_agent.agent.trace_log import redact_trace_value
from calculus_agent.runtime.model_turn import ModelTurnPreparation
from calculus_agent.runtime.tool_loop import ToolLoop


def execute_model_turn(
    *,
    backend: Any,
    messages: list[dict],
    definitions: list[dict],
    preparation: ModelTurnPreparation,
    run_manager: Any,
    parent_span_id: str | None,
    forced_response: dict | None = None,
) -> dict:
    """Execute one prepared completion and record its model span.

    ``forced_response`` preserves deterministic runtime boundaries (for example,
    a cancellation that must not be delegated back to the model) while keeping
    span accounting identical to ordinary completions.
    """
    model_span = run_manager.add_span(
        "model_call", "llm_completion", parent_span_id=parent_span_id,
        input=preparation.span_input,
    )
    started_at = datetime.now(UTC)
    with llm_generation_span(backend, messages, definitions) as langfuse_span:
        try:
            if forced_response is not None:
                response = forced_response
                metadata = {
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "cached_tokens": None,
                    "reasoning_tokens": None,
                    "actual_usage_available": False,
                    "model_called": False,
                    "provider": None,
                    "model": None,
                    "provider_usage_raw": None,
                    "provider_metadata": {"forced_response": True},
                }
            else:
                completion = ToolLoop.run_result(backend, messages, definitions)
                response = completion.message
                metadata = {
                    **completion.metadata,
                    "model_called": True,
                    "backend_adapter": getattr(
                        backend, "backend_adapter", type(backend).__name__
                    ),
                    "protocol": getattr(backend, "protocol", None),
                }
            ended_at = datetime.now(UTC)
            span_output = {
                "tool_calls": len(response.get("tool_calls") or []),
                "tool_names": [
                    (call.get("function") or {}).get("name")
                    for call in (response.get("tool_calls") or [])
                ],
                "tool_round": preparation.span_input["tool_round"],
                "context_metrics": preparation.context_metrics.as_dict(),
                "llm_latency_ms": int((ended_at - started_at).total_seconds() * 1000),
                **metadata,
            }
            run_manager.update_span(
                model_span,
                status="success",
                output=span_output,
                ended_at=ended_at,
            )
        except Exception as exc:
            safe_update(langfuse_span, level="ERROR", status_message=str(exc))
            run_manager.update_span(
                model_span,
                status="error",
                output={
                    "error": str(exc),
                    "model_called": forced_response is None,
                    "actual_usage_available": False,
                    "provider": getattr(backend, "provider", None),
                    "model": getattr(backend, "model", None),
                    "backend_adapter": getattr(
                        backend, "backend_adapter", type(backend).__name__
                    ),
                    "protocol": getattr(backend, "protocol", None),
                },
                ended_at=datetime.now(UTC),
            )
            raise
        safe_update(
            langfuse_span,
            output={"response": redact_trace_value(response)},
            usage_details={
                key: metadata[key]
                for key in ("input_tokens", "output_tokens", "total_tokens")
                if metadata.get(key) is not None
            },
            metadata={
                key: metadata.get(key)
                for key in ("provider", "model", "cached_tokens", "reasoning_tokens")
                if metadata.get(key) is not None
            },
        )
    return response
