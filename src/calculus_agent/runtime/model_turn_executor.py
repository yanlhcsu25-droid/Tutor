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
            response = forced_response or ToolLoop.run(backend, messages, definitions)
            ended_at = datetime.now(UTC)
            run_manager.update_span(
                model_span,
                status="success",
                output={
                    "tool_calls": len(response.get("tool_calls") or []),
                    "tool_names": [
                        (call.get("function") or {}).get("name")
                        for call in (response.get("tool_calls") or [])
                    ],
                    "tool_round": preparation.span_input["tool_round"],
                    "context_metrics": preparation.context_metrics.as_dict(),
                    "llm_latency_ms": int((ended_at - started_at).total_seconds() * 1000),
                },
                ended_at=ended_at,
            )
        except Exception as exc:
            safe_update(langfuse_span, level="ERROR", status_message=str(exc))
            run_manager.update_span(
                model_span, status="error", output={"error": str(exc)},
                ended_at=datetime.now(UTC),
            )
            raise
        safe_update(langfuse_span, output={"response": redact_trace_value(response)})
    return response
