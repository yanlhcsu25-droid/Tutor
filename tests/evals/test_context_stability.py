from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import mean

import pytest

from tests.evals.case_loader import load_eval_suite
from tests.evals.runner import run_case


CASE_FILE = Path(__file__).parent / "context_stability.yaml"
REPORT_FILE = Path(__file__).parent / "reports" / "context_stability.json"


def test_context_stability_scenario_is_well_formed() -> None:
    suite = load_eval_suite(CASE_FILE)
    assert suite.name == "teacher_agent_context_stability"
    assert len(suite.cases) == 1
    case = suite.cases[0]
    assert case.category == "context_stability"
    assert len(case.turns) >= 5
    assert all(isinstance(turn.get("user"), str) for turn in case.turns)


def _baseline(result: dict) -> dict:
    turns = []
    for turn in result.get("turns", []):
        observations = turn.get("observability") or {}
        metrics = observations.get("context_metrics") or []
        latencies = [value for value in observations.get("latency_ms", []) if value is not None]
        model_calls = observations.get("model_calls") or []
        tool_calls = observations.get("tool_calls") or []

        tool_observation_details = []
        observation_size_metrics = []
        tool_timeline = []
        for span in tool_calls:
            output = span.get("output_json") or {}

            payload = output.get("payload")
            if isinstance(output.get("_observation_metrics"), dict):
                observation_size_metrics.append(output["_observation_metrics"])

            tool_timeline.append(
                {
                    "tool_name": span.get("name"),
                    "status": output.get("status"),
                    "latency_ms": span.get("latency_ms"),
                }
            )

            if payload is not None:
                tool_observation_details.append(
                    {
                        "tool_name": span.get("name"),
                        "chars": len(
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                            )
                        ),
                    }
                )
        tool_names = []
        for span in model_calls:
            output = span.get("output_json") or {}
            tool_names.extend(output.get("tool_names") or [])
        totals = [item.get("total_chars", 0) for item in metrics if isinstance(item, dict)]
        breakdowns = [
            item.get("context_breakdown", {})
            for item in metrics
            if isinstance(item, dict)
        ]
        workspace_breakdowns = [
            item.get("workspace_breakdown", {})
            for item in metrics
            if isinstance(item, dict)
        ]
        workspace_detail_breakdowns = [
            item.get("workspace_detail_breakdown", {})
            for item in metrics
            if isinstance(item, dict)
        ]
        turns.append(
            {
                "turn": turn.get("turn"),
                "context_chars": max(totals, default=0),
                "context_breakdown": breakdowns[-1] if breakdowns else {},
                "workspace_breakdown": (
                    workspace_breakdowns[-1] if workspace_breakdowns else {}
                ),
                "workspace_detail_breakdown": (
                    workspace_detail_breakdowns[-1]
                    if workspace_detail_breakdowns else {}
                ),
                "estimated_tokens": max(
                    (
                        item.get("estimated_tokens", 0)
                        for item in metrics
                        if isinstance(item, dict)
                    ),
                    default=0,
                ),
                "latency_ms": sum(latencies),
                "tool_round": max(
                    observations.get("tool_rounds") or [0]
                ),
                "tool_calls": tool_names,
                "tool_timeline": tool_timeline,
                "observation_size_metrics": observation_size_metrics,
                "runtime_success": (
                    turn.get("state") or {}
                ).get("status") not in {"failed", None},
            }
        )

    context_values = [turn["context_chars"] for turn in turns if turn["context_chars"]]
    latency_values = [turn["latency_ms"] for turn in turns if turn["latency_ms"]]
    return {
        "turns": turns,
        "context_growth_chars": (context_values[-1] - context_values[0]) if len(context_values) > 1 else 0,
        "average_latency_ms": mean(latency_values) if latency_values else 0,
        "tool_call_count": sum(len(turn["tool_calls"]) for turn in turns),
        "tool_sequence": [
            item["tool_name"]
            for turn in turns
            for item in turn["tool_timeline"]
        ],
        "total_tool_latency_ms": sum(
            item["latency_ms"] or 0
            for turn in turns
            for item in turn["tool_timeline"]
        ),
        "runtime_success": bool(turns) and all(turn["runtime_success"] for turn in turns),
    }


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Long-task stability baseline requires RUN_LIVE_LLM=1",
)
def test_long_task_context_stability_baseline() -> None:
    suite = load_eval_suite(CASE_FILE)
    result = run_case(suite.cases[0])
    report = {
        "suite": suite.name,
        "case_id": suite.cases[0].id,
        "baseline": _baseline(result),
        "runtime_error": result.get("error"),
    }
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Long Task Stability Report:")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert result.get("error") is None
