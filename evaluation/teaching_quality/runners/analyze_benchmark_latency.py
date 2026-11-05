"""Aggregate non-overlapping timing categories available in benchmark traces."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


def breakdown(row: dict[str, Any]) -> dict[str, Any]:
    result = row.get("result") or {}
    totals = {"model": 0.0, "tool": 0.0, "database_state_transition": 0.0}
    calls = {"model": 0, "tool": 0, "database_state_transition": 0}
    for turn in result.get("turns") or []:
        for span in (turn.get("trace") or {}).get("spans") or []:
            kind = {"model_call": "model", "tool_call": "tool", "state_transition": "database_state_transition"}.get(span.get("type"))
            if kind:
                totals[kind] += float(span.get("latency_ms") or 0) / 1000
                calls[kind] += 1
    elapsed = float(result.get("elapsed_seconds") or 0)
    judge = (result.get("grading") or {}).get("judge_elapsed_seconds")
    return {
        "case_id": row.get("case_id"), "run_id": row.get("run_id"),
        "agent_workflow_seconds": elapsed, "seconds": totals, "calls": calls,
        "judge_seconds": judge,
        "retry_wait_seconds": "unavailable: backend does not emit retry/wait spans",
        "other_agent_residual_seconds": max(0.0, elapsed - sum(totals.values())),
        "database_scope_note": "Only explicit state_transition spans. DB work inside tools is inseparable from tool time in historical traces.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.runs.read_text().splitlines() if line.strip()]
    runs = [breakdown(row) for row in rows]
    cases = {}
    for case_id in sorted({run["case_id"] for run in runs}):
        selected = [run for run in runs if run["case_id"] == case_id]
        cases[case_id] = {
            "runs": len(selected),
            "mean_agent_workflow_seconds": statistics.mean(run["agent_workflow_seconds"] for run in selected),
            "mean_model_seconds": statistics.mean(run["seconds"]["model"] for run in selected),
            "mean_tool_seconds": statistics.mean(run["seconds"]["tool"] for run in selected),
            "mean_database_state_transition_seconds": statistics.mean(run["seconds"]["database_state_transition"] for run in selected),
            "mean_other_agent_residual_seconds": statistics.mean(run["other_agent_residual_seconds"] for run in selected),
            "judge_seconds": "unavailable in benchmark-v1 artifacts",
            "retry_wait_seconds": "unavailable in benchmark-v1 artifacts",
        }
    output = {
        "source": str(args.runs), "categories_are_non_overlapping": True,
        "historical_limitations": [
            "Judge latency was not persisted in benchmark-v1.",
            "Backend retry and wait time was not emitted separately; it is included in model-call latency.",
            "SQL executed inside tool calls cannot be separated retrospectively.",
        ],
        "cases": cases, "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
