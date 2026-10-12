from unittest.mock import patch

from tests.evals.token_optimization.token_baseline_audit import audit_report


def _model(span_id, started_at, *, tool_calls, input_tokens, attempts=None):
    metadata = {"provider_attempts": attempts} if attempts is not None else {}
    return {
        "span_id": span_id,
        "started_at": started_at,
        "input_json": {
            "n_definitions": 2,
            "tool_round": 0,
            "context_metrics": {
                "total_chars": 100,
                "tool_schema_chars": 40,
                "context_breakdown": {
                    "system_prompt_chars": 20,
                    "tool_schema_chars": 40,
                    "conversation_chars": 10,
                    "workspace_chars": 10,
                    "observation_chars": 15,
                    "other_chars": 5,
                },
                "workspace_breakdown": {"memory_chars": 10},
            },
        },
        "output_json": {
            "tool_calls": tool_calls,
            "input_tokens": input_tokens,
            "output_tokens": 5,
            "total_tokens": input_tokens + 5,
            "provider_metadata": metadata,
        },
    }


def test_audit_keeps_provider_tokens_separate_and_deduplicates_trace_projections():
    models = [
        _model("m1", "01", tool_calls=0, input_tokens=70, attempts=[{}, {}]),
        _model("m2", "04", tool_calls=1, input_tokens=80),
    ]
    tools = [
        {
            "span_id": "t1",
            "name": "read_paper",
            "started_at": "02",
            "ended_at": "03",
            "input_json": {"arguments": {}},
            "output_json": {"ok": False},
        },
        {
            "span_id": "t2",
            "name": "read_paper",
            "started_at": "05",
            "ended_at": "06",
            "input_json": {"arguments": {}},
            "output_json": {"ok": True},
        },
    ]
    turn = {"observability": {"model_calls": models, "tool_calls": tools}}
    payload = {
        "results": [{
            "case_id": "CASE-1",
            "category": "paper",
            "passed": True,
            "model_calls": 2,
            "tool_calls": 2,
            "input_tokens": 150,
            "output_tokens": 10,
            "total_tokens": 160,
            "latency_ms": 50,
            "actual": {
                "status": "completed",
                "result": {"blocking_errors": ["blocked"]},
                "evaluated_turns": [turn],
            },
            "turns": [turn],
        }]
    }

    with patch(
        "tests.evals.token_optimization.token_baseline_audit._current_tool_schemas",
        return_value=[{"name": "read_paper", "schema_chars": 40}],
    ):
        report = audit_report(payload, source_path="fixture.json", source_sha256="abc")

    assert report["overview"]["input_tokens"]["sum"] == 150
    assert report["context_size_proxy"]["shares_on_reconciled_calls_pct"] == {
        "system_prompt_chars": 20.0,
        "tool_schema_chars": 40.0,
        "conversation_chars": 10.0,
        "workspace_chars": 10.0,
        "observation_chars": 15.0,
        "other_chars": 5.0,
    }
    assert report["loop_audit"]["intermediate_no_tool_output_then_recheck"]["count"] == 1
    assert len(report["loop_audit"]["exact_repeated_tool_calls_same_turn"]) == 1
    assert len(report["loop_audit"]["failed_tool_then_model_continuations"]) == 1
    assert report["provider_attempts"]["retry_attempts"] == 1
    assert report["duplicate_trace_detection"]["model_calls"]["duplicate_projection_records"] == 2
