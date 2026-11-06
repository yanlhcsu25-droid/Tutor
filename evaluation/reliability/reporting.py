"""Suite summaries and compatible multi-report aggregation."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.reliability.execution_control import EvaluationControlError

ATOMIC_GRADERS = (
    "state",
    "score",
    "workflow_safety",
    "acceptance",
    "constraint_preservation",
    "outcome_integrity",
    "clarification_contract",
    "content_consistency",
)
EXECUTION_STATUSES = (
    "passed",
    "agent_failed",
    "infrastructure_failed",
    "fixture_failed",
    "runner_failed",
    "skipped",
)


def _status(result: dict[str, Any]) -> str:
    status = result.get("execution_status")
    if status in EXECUTION_STATUSES:
        return status
    return "passed" if result.get("passed") is True else "agent_failed"


def summarize_graders(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = list(ATOMIC_GRADERS)
    extras = sorted({
        str(grader.get("grader"))
        for result in results
        for grader in (result.get("graders") or [])
        if grader.get("grader") and grader.get("grader") not in ATOMIC_GRADERS
    })
    summaries: dict[str, dict[str, Any]] = {}
    for name in [*names, *extras]:
        counts = Counter({
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "not_applicable": 0,
            "not_executed": 0,
        })
        observed_cases: set[str] = set()
        for result in results:
            graders = [
                grader for grader in (result.get("graders") or [])
                if grader.get("grader") == name
            ]
            if not graders:
                counts["not_executed"] += 1
                continue
            observed_cases.add(str(result.get("case_id")))
            for grader in graders:
                if grader.get("skipped") is True:
                    counts["skipped"] += 1
                elif grader.get("eligible") is False:
                    counts["not_applicable"] += 1
                elif grader.get("passed") is True:
                    counts["passed"] += 1
                else:
                    counts["failed"] += 1
        executed = counts["passed"] + counts["failed"]
        summaries[name] = {
            "observed_cases": len(observed_cases),
            "executed": executed,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "not_applicable": counts["not_applicable"],
            "not_executed": counts["not_executed"],
            "pass_rate": counts["passed"] / executed if executed else None,
        }
    return summaries


def summarize_results(
    results: list[dict[str, Any]],
    *,
    mode: str = "diagnostic",
    expected_cases: int | None = None,
) -> dict[str, Any]:
    statuses = Counter(_status(result) for result in results)
    passed = statuses["passed"]
    agent_failed = statuses["agent_failed"]
    denominator = passed + agent_failed
    agent_pass_rate = passed / denominator if denominator else None
    non_agent_incomplete = sum(
        statuses[status]
        for status in (
            "infrastructure_failed", "fixture_failed", "runner_failed", "skipped"
        )
    )
    expected = len(results) if expected_cases is None else expected_cases
    return {
        "cases": len(results),
        "completed_cases": len(results),
        "expected_cases": expected,
        "passed": passed,
        # Backward-compatible field with corrected Agent-failure semantics.
        "failed": agent_failed,
        "pass_rate": agent_pass_rate,
        "agent_denominator": denominator,
        "agent_pass_rate": agent_pass_rate,
        "benchmark_pass_rate": agent_pass_rate if mode == "benchmark" else None,
        "benchmark_eligible": mode == "benchmark",
        "benchmark_complete": (
            mode == "benchmark"
            and len(results) == expected
            and non_agent_incomplete == 0
        ),
        "total_not_passed": len(results) - passed,
        "execution_status": {
            status: statuses[status] for status in EXECUTION_STATUSES
        },
        "graders": summarize_graders(results),
    }


def _compatibility_fingerprint(report: dict[str, Any]) -> dict[str, Any]:
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        raise EvaluationControlError("report lacks metadata required for aggregation")
    reproducibility = metadata.get("reproducibility") or {}
    execution = metadata.get("execution") or {}
    components: dict[str, Any] = {}
    for name in (
        "runtime", "agent", "tools", "services", "prompt", "graders",
        "fixture", "runner", "model_configuration",
    ):
        value = reproducibility.get(name)
        if not isinstance(value, dict) or not value.get("sha256"):
            raise EvaluationControlError(f"report lacks reproducibility.{name}.sha256")
        components[name] = value["sha256"]
    if not metadata.get("git_sha"):
        raise EvaluationControlError("report lacks git_sha")
    required_execution = (
        "mode", "dataset_role", "variant", "model_ids", "providers",
        "backend_adapters", "protocols", "temperatures", "timeout_seconds",
        "max_retries", "retry_base_seconds", "case_delay_seconds",
        "langfuse_enabled",
    )
    for key in required_execution:
        if key not in execution:
            raise EvaluationControlError(f"report lacks execution.{key}")
    return {
        "git_sha": metadata["git_sha"],
        "git_dirty": metadata.get("git_dirty"),
        "components": components,
        **{key: execution[key] for key in required_execution},
    }


def aggregate_reports(report_paths: list[str | Path]) -> dict[str, Any]:
    if len(report_paths) < 2:
        raise EvaluationControlError("aggregation requires at least two reports")
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for raw_path in report_paths:
        path = Path(raw_path).resolve()
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationControlError(f"cannot read report {path}: {exc}") from exc
        if not isinstance(report, dict) or not isinstance(report.get("results"), list):
            raise EvaluationControlError(f"invalid report schema: {path}")
        loaded.append((path, report))

    first_fingerprint = _compatibility_fingerprint(loaded[0][1])
    if first_fingerprint["dataset_role"] != "development":
        raise EvaluationControlError("only independent development reports may be aggregated")
    run_ids: set[str] = set()
    dataset_hashes: set[str] = set()
    case_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for path, report in loaded:
        fingerprint = _compatibility_fingerprint(report)
        if fingerprint != first_fingerprint:
            raise EvaluationControlError(
                f"incompatible report configuration: {path}"
            )
        metadata = report["metadata"]
        run_id = metadata.get("run_id")
        dataset = metadata.get("dataset") or {}
        dataset_hash = dataset.get("sha256")
        if not run_id or not dataset_hash:
            raise EvaluationControlError(f"report lacks run_id/dataset hash: {path}")
        if run_id in run_ids:
            raise EvaluationControlError(f"duplicate run_id in aggregate: {run_id}")
        if dataset_hash in dataset_hashes:
            raise EvaluationControlError(
                f"reports must represent independent datasets: {dataset_hash}"
            )
        run_ids.add(run_id)
        dataset_hashes.add(dataset_hash)
        current_results = report["results"]
        if dataset.get("case_count") != len(current_results):
            raise EvaluationControlError(f"source report is incomplete: {path}")
        current_ids = {str(result.get("case_id")) for result in current_results}
        overlap = sorted(case_ids & current_ids)
        if overlap:
            raise EvaluationControlError(f"duplicate case IDs across reports: {overlap}")
        case_ids.update(current_ids)
        results.extend(current_results)
        sources.append({
            "path": str(path),
            "run_id": run_id,
            "dataset": dataset,
            "cases": len(current_results),
        })

    mode = first_fingerprint["mode"]
    return {
        "report_type": "multi_dataset_aggregate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "source_reports": sources,
            "compatibility": first_fingerprint,
            "aggregation_note": "Source reports remain the primary immutable records.",
        },
        "summary": summarize_results(results, mode=mode),
        "results": results,
    }
