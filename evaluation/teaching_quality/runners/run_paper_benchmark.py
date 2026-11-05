"""Serial repeated benchmark for the three seeded paper-quality cases."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation.teaching_quality.runners.run_dev_eval import (  # noqa: E402
    atomic_json, grade_result, is_provider_exception, load_dev_cases, run_case, source_database,
)

SUPPORTED_CASES = ("TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003")
FIXTURE_MANIFESTS = {
    "TQ-PQ-001": ROOT / "fixtures/question_bank/basic_v1.manifest.json",
    "TQ-PQ-002": ROOT / "fixtures/question_bank/chapter3_rich_v1.manifest.json",
    "TQ-PQ-003": ROOT / "fixtures/question_bank/first_three_chapters_open_v1.manifest.json",
}


def _run_metrics(result: dict[str, Any]) -> dict[str, Any]:
    turns = result.get("turns") or []
    tokens = 0
    usage_available = False
    tool_calls = 0
    span_seconds = {"model": 0.0, "tool": 0.0, "database": 0.0}
    for turn in turns:
        trace = turn.get("trace") or {}
        tool_calls += len(trace.get("tool_calls") or [])
        for span in trace.get("spans") or []:
            output = span.get("output") or {}
            span_type = span.get("type")
            latency = float(span.get("latency_ms") or 0) / 1000
            if span_type == "model_call":
                span_seconds["model"] += latency
                if output.get("actual_usage_available"):
                    usage_available = True
                    tokens += int(output.get("total_tokens") or 0)
            elif span_type == "tool_call":
                span_seconds["tool"] += latency
            elif span_type == "state_transition":
                span_seconds["database"] += latency
    grading = result.get("grading") or {}
    score = grading.get("score") or {}
    structural = grading.get("structural") or []
    behavioral = grading.get("behavioral") or {}
    judge_provider = grading.get("judge_provider") or {}
    saved_judge_calls = judge_provider.get("calls") or []
    if not saved_judge_calls and judge_provider.get("last_call"):
        saved_judge_calls = [judge_provider["last_call"]]
    judge_tokens = [
        (call.get("usage") or {}).get("total_tokens")
        for call in saved_judge_calls
        if isinstance((call.get("usage") or {}).get("total_tokens"), int)
    ]
    request_completed = bool(
        result.get("request_completed")
        and result.get("actual_output", {}).get("paper")
        and (result.get("workflow_checks") or {}).get("paper_persisted")
    )
    structural_pass = bool(structural) and all(item.get("passed") for item in structural)
    judge_valid = grading.get("judge_validity") == "VALID" and bool(score)
    quality_pass = judge_valid and score.get("status") == "PASS"
    strict_e2e_pass = request_completed and structural_pass and quality_pass
    if not request_completed:
        final_outcome = "agent_business_execution_failure"
    elif not structural_pass:
        final_outcome = "structural_failure"
    elif not judge_valid:
        final_outcome = "judge_invalid_or_unscored"
    elif not quality_pass:
        final_outcome = "valid_judge_quality_failure"
    else:
        final_outcome = "complete_pass"
    return {
        "agent_tokens": tokens if usage_available else None,
        "tool_calls": tool_calls,
        "quality_score_raw": score.get("weighted_score") if judge_valid else None,
        "judge_outcome": score.get("status") if judge_valid else None,
        "request_completed": request_completed,
        "workflow_completed": request_completed,
        "judge_valid": judge_valid,
        "judge_contract_violation": grading.get("judge_contract_violation") is True,
        "quality_pass": quality_pass,
        "strict_e2e_pass": strict_e2e_pass,
        "e2e_success": strict_e2e_pass,
        "final_outcome": final_outcome,
        "execution_outcome": result.get("execution_outcome", "recovery_failure"),
        "judge_tokens": sum(judge_tokens) if judge_tokens else None,
        "structural_pass": structural_pass,
        "critical_quality_failure": behavioral.get("critical_quality_failure") is True,
        "execution_mode": result.get("execution_mode", "unknown"),
        "clarification_required": result.get("clarification_required") is True,
        "timing_seconds": {
            **span_seconds,
            "judge": grading.get("judge_elapsed_seconds"),
            "retry_wait": "unavailable",
            "other_agent_residual": max(
                0.0,
                float(result.get("elapsed_seconds") or 0)
                - span_seconds["model"] - span_seconds["tool"] - span_seconds["database"],
            ),
        },
    }


def _p95(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(record["case_id"], []).append(record)
    cases = {}
    for case_id, rows in by_case.items():
        scores = [row["metrics"]["quality_score_raw"] for row in rows if row["metrics"]["quality_score_raw"] is not None]
        latencies = [float(row["result"]["elapsed_seconds"]) for row in rows if row.get("result", {}).get("elapsed_seconds") is not None]
        token_values = [row["metrics"]["agent_tokens"] for row in rows if row["metrics"]["agent_tokens"] is not None]
        total = len(rows)
        completed = sum(row["metrics"].get("workflow_completed", False) for row in rows)
        judge_pass = sum(row["metrics"].get("strict_e2e_pass", False) for row in rows)
        structural_pass = sum(row["metrics"]["structural_pass"] for row in rows)
        judge_valid = sum(row["metrics"].get("judge_valid", False) for row in rows)
        contract_violations = sum(
            row["metrics"].get("judge_contract_violation", False) for row in rows
        )
        quality_pass = sum(row["metrics"].get("quality_pass", False) for row in rows)
        critical = sum(row["metrics"]["critical_quality_failure"] for row in rows)
        paper_orders = [
            tuple(question["id"] for question in row["result"]["actual_output"]["paper"]["questions"])
            for row in rows if row.get("result", {}).get("actual_output", {}).get("paper")
        ]
        paper_sets = [frozenset(order) for order in paper_orders]
        pairwise_jaccard = [
            len(left & right) / len(left | right)
            for index, left in enumerate(paper_sets) for right in paper_sets[index + 1:]
            if left | right
        ]
        mode_metrics = {}
        for mode in ("unassisted", "assisted"):
            mode_rows = [row for row in rows if row["metrics"].get("execution_mode") == mode]
            mode_metrics[mode] = {
                "total_runs": len(mode_rows),
                "judge_pass_runs": sum(
                    row["metrics"].get("strict_e2e_pass", False) for row in mode_rows
                ),
                "e2e_pass_rate": (
                    sum(row["metrics"].get("strict_e2e_pass", False) for row in mode_rows)
                    / len(mode_rows) if mode_rows else None
                ),
                "clarification_required_rate": (
                    sum(row["metrics"].get("clarification_required", False) for row in mode_rows)
                    / len(mode_rows) if mode_rows else None
                ),
            }
        cases[case_id] = {
            "quality": {
                "valid_scored_runs": len(scores),
                "mean_quality_score_raw": statistics.mean(scores) if scores else None,
                "mean_quality_score_pct": statistics.mean(scores) / 5 * 100 if scores else None,
                "quality_score_std": statistics.pstdev(scores) if scores else None,
                "min_quality_score": min(scores) if scores else None,
                "max_quality_score": max(scores) if scores else None,
            },
            "reliability": {
                "total_runs": total,
                "completed_runs": completed,
                "judge_pass_runs": judge_pass,
                "workflow_completion_rate": completed / total if total else 0,
                "structural_pass_rate": structural_pass / total if total else 0,
                "judge_valid_rate": judge_valid / total if total else 0,
                "judge_contract_violation_rate": contract_violations / total if total else 0,
                "quality_pass_rate": quality_pass / judge_valid if judge_valid else None,
                "quality_pass_denominator": judge_valid,
                "strict_e2e_pass_rate": judge_pass / total if total else 0,
                "e2e_pass_rate": judge_pass / total if total else 0,
                "critical_failure_rate": critical / total if total else 0,
                "by_execution_mode": mode_metrics,
            },
            "generation_stability": {
                "generated_papers": len(paper_sets),
                "unique_question_sets": len(set(paper_sets)),
                "unique_question_orders": len(set(paper_orders)),
                "dominant_question_set_rate": (
                    max(Counter(paper_sets).values()) / len(paper_sets) if paper_sets else None
                ),
                "mean_pairwise_question_jaccard": (
                    statistics.mean(pairwise_jaccard) if pairwise_jaccard else None
                ),
            },
            "performance": {
                "avg_latency_seconds": statistics.mean(latencies) if latencies else None,
                "p95_latency_seconds": _p95(latencies),
                "p95_method": "nearest_rank" if len(latencies) >= 2 else "insufficient_samples",
                "avg_agent_tokens": statistics.mean(token_values) if token_values else None,
                "avg_tool_calls": statistics.mean(row["metrics"]["tool_calls"] for row in rows) if rows else None,
                "total_agent_tokens": sum(token_values),
                "agent_token_measured_runs": len(token_values),
                "judge_tokens": sum(
                    row["metrics"]["judge_tokens"] for row in rows
                    if row["metrics"].get("judge_tokens") is not None
                ) or None,
                "judge_token_measured_runs": sum(
                    row["metrics"].get("judge_tokens") is not None for row in rows
                ),
            },
            "outcomes": dict(Counter(row["metrics"]["judge_outcome"] or "UNSCORED" for row in rows)),
            "execution_outcomes": dict(Counter(
                row["metrics"].get("execution_outcome", "unknown") for row in rows
            )),
            "final_outcomes": dict(Counter(
                row["metrics"].get("final_outcome", "unknown") for row in rows
            )),
        }
    all_scores = [row["metrics"]["quality_score_raw"] for row in records if row["metrics"]["quality_score_raw"] is not None]
    all_latencies = [float(row["result"]["elapsed_seconds"]) for row in records if row.get("result", {}).get("elapsed_seconds") is not None]
    all_tokens = [row["metrics"]["agent_tokens"] for row in records if row["metrics"]["agent_tokens"] is not None]
    total = len(records)
    overall_modes = {}
    for mode in ("unassisted", "assisted"):
        mode_rows = [row for row in records if row["metrics"].get("execution_mode") == mode]
        passes = sum(row["metrics"].get("strict_e2e_pass", False) for row in mode_rows)
        overall_modes[mode] = {
            "total_runs": len(mode_rows),
            "judge_pass_runs": passes,
            "e2e_pass_rate": passes / len(mode_rows) if mode_rows else None,
            "clarification_required_rate": (
                sum(row["metrics"].get("clarification_required", False) for row in mode_rows)
                / len(mode_rows) if mode_rows else None
            ),
        }
    overall_completed = sum(
        row["metrics"].get("workflow_completed", False) for row in records
    )
    overall_structural = sum(row["metrics"]["structural_pass"] for row in records)
    overall_judge_valid = sum(
        row["metrics"].get("judge_valid", False) for row in records
    )
    overall_contract_violations = sum(
        row["metrics"].get("judge_contract_violation", False) for row in records
    )
    overall_quality_pass = sum(
        row["metrics"].get("quality_pass", False) for row in records
    )
    overall_strict_pass = sum(
        row["metrics"].get("strict_e2e_pass", False) for row in records
    )
    overall = {
        "quality": {
            "valid_scored_runs": len(all_scores),
            "mean_quality_score_raw": statistics.mean(all_scores) if all_scores else None,
            "mean_quality_score_pct": statistics.mean(all_scores) / 5 * 100 if all_scores else None,
            "quality_score_std": statistics.pstdev(all_scores) if all_scores else None,
            "min_quality_score": min(all_scores) if all_scores else None,
            "max_quality_score": max(all_scores) if all_scores else None,
        },
        "reliability": {
            "total_runs": total,
            "completed_runs": overall_completed,
            "judge_pass_runs": overall_strict_pass,
            "workflow_completion_rate": overall_completed / total if total else 0,
            "structural_pass_rate": overall_structural / total if total else 0,
            "judge_valid_rate": overall_judge_valid / total if total else 0,
            "judge_contract_violation_rate": overall_contract_violations / total if total else 0,
            "quality_pass_rate": (
                overall_quality_pass / overall_judge_valid if overall_judge_valid else None
            ),
            "quality_pass_denominator": overall_judge_valid,
            "strict_e2e_pass_rate": overall_strict_pass / total if total else 0,
            "e2e_pass_rate": overall_strict_pass / total if total else 0,
            "critical_failure_rate": sum(row["metrics"]["critical_quality_failure"] for row in records) / total if total else 0,
            "by_execution_mode": overall_modes,
        },
        "performance": {
            "avg_latency_seconds": statistics.mean(all_latencies) if all_latencies else None,
            "p95_latency_seconds": _p95(all_latencies),
            "p95_method": "nearest_rank" if len(all_latencies) >= 2 else "insufficient_samples",
            "avg_agent_tokens": statistics.mean(all_tokens) if all_tokens else None,
            "avg_tool_calls": statistics.mean(row["metrics"]["tool_calls"] for row in records) if records else None,
            "total_agent_tokens": sum(all_tokens),
            "agent_token_measured_runs": len(all_tokens),
            "judge_tokens": sum(
                row["metrics"]["judge_tokens"] for row in records
                if row["metrics"].get("judge_tokens") is not None
            ) or None,
            "judge_token_measured_runs": sum(
                row["metrics"].get("judge_tokens") is not None for row in records
            ),
        },
    }
    return {"cases": cases, "overall": overall}


def _markdown(summary: dict[str, Any], judge_stability: dict[str, Any] | None) -> str:
    lines = [
        "# Paper Quality Repeated Benchmark", "",
        "Quality means valid Judge weighted score, not accuracy. Failed/unscored runs remain in reliability denominators.", "",
        "| Case | Valid scored | Mean / 5 | Mean % | Std | E2E pass | Structural pass | Avg seconds | P95 seconds | Avg tokens | Avg tools |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case_id, item in summary["cases"].items():
        q, r, p = item["quality"], item["reliability"], item["performance"]
        fmt = lambda value: "n/a" if value is None else f"{value:.3f}"  # noqa: E731
        lines.append(
            f"| {case_id} | {q['valid_scored_runs']} | {fmt(q['mean_quality_score_raw'])} | "
            f"{fmt(q['mean_quality_score_pct'])} | {fmt(q['quality_score_std'])} | "
            f"{r['e2e_pass_rate']:.1%} | {r['structural_pass_rate']:.1%} | "
            f"{fmt(p['avg_latency_seconds'])} | {fmt(p['p95_latency_seconds'])} | "
            f"{fmt(p['avg_agent_tokens'])} | {fmt(p['avg_tool_calls'])} |"
        )
    overall = summary["overall"]
    mean_raw = overall["quality"]["mean_quality_score_raw"]
    mean_pct = overall["quality"]["mean_quality_score_pct"]
    reliability = overall["reliability"]
    rate = lambda value: "n/a" if value is None else f"{value:.1%}"  # noqa: E731
    lines.extend([
        "",
        f"Overall valid-score mean: {'n/a' if mean_raw is None else f'{mean_raw:.3f}/5'} "
        f"({'n/a' if mean_pct is None else f'{mean_pct:.3f}%'}, "
        f"n={overall['quality']['valid_scored_runs']}); "
        f"strict E2E pass: {reliability['judge_pass_runs']}/{reliability['total_runs']} "
        f"({rate(reliability['strict_e2e_pass_rate'])}).",
        "",
        "## Reliability stages",
        "",
        f"- Workflow completion: {rate(reliability['workflow_completion_rate'])}",
        f"- Structural pass: {rate(reliability['structural_pass_rate'])}",
        f"- Judge valid: {rate(reliability['judge_valid_rate'])}",
        f"- Judge contract violation: {rate(reliability['judge_contract_violation_rate'])}",
        f"- Quality pass among valid Judges: {rate(reliability['quality_pass_rate'])}",
        f"- Strict E2E pass: {rate(reliability['strict_e2e_pass_rate'])}",
        "", "## Generation stability", "",
    ])
    for case_id, item in summary["cases"].items():
        stability = item["generation_stability"]
        lines.append(
            f"- `{case_id}`: generated={stability['generated_papers']}, "
            f"unique sets={stability['unique_question_sets']}, "
            f"unique orders={stability['unique_question_orders']}, "
            f"dominant-set rate={stability['dominant_question_set_rate']}, "
            f"mean pairwise Jaccard={stability['mean_pairwise_question_jaccard']}."
        )
    lines.extend([
        "", "Judge token usage is aggregated from saved provider usage when available; never estimated."
    ])
    if judge_stability:
        lines.extend([
            "", "## Fixed-paper Judge stability", "",
            f"Case: `{judge_stability['case_id']}`; repeats: {judge_stability['repeats']}; "
            f"scores: `{judge_stability['scores']}`; population std: `{judge_stability['score_std']}`.",
            "", f"Axis scores: `{json.dumps(judge_stability['axis_scores'], ensure_ascii=False)}`",
        ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serial repeated benchmark for seeded paper cases")
    parser.add_argument("--case-id", action="append", choices=SUPPORTED_CASES)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--fixture-version", default="frozen-current")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--agent-model")
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-version", choices=("v1", "v2"), default="v2")
    parser.add_argument("--execution-mode", choices=("unassisted", "assisted"), default="unassisted")
    parser.add_argument("--max-tool-rounds", type=int, default=8)
    parser.add_argument("--pacing-seconds", type=float, default=1)
    parser.add_argument("--judge-stability-repeats", type=int, default=3)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required")
    if args.repeats < 1 or args.judge_stability_repeats < 1:
        parser.error("repeat counts must be positive")
    if args.fixture_version != "frozen-current":
        parser.error("only fixture_version=frozen-current is available")
    if args.output_dir.exists():
        parser.error("output directory already exists")
    args.output_dir.mkdir(parents=True)

    model_config = json.loads(args.model_config.read_text()) if args.model_config else {}
    agent_model = args.agent_model or model_config.get("agent_model")
    judge_model = args.judge_model or model_config.get("judge_model")
    case_ids = args.case_id or list(SUPPORTED_CASES)
    cases, _ = load_dev_cases()
    case_by_id = {case["case_id"]: case for case in cases}

    from calculus_agent.config import Settings
    current_settings = Settings()
    source = source_database(current_settings)
    if source is None:
        raise RuntimeError("real SQLite source database is required")
    fixture_hashes = {}
    for case_id in case_ids:
        manifest = json.loads(FIXTURE_MANIFESTS[case_id].read_text())
        if manifest["case_id"] != case_id or not manifest["validation_passed"]:
            raise ValueError(f"invalid frozen fixture manifest: {case_id}")
        fixture_hashes[case_id] = manifest["manifest_sha256"]

    metadata = {
        "kind": "paper_quality_repeated_benchmark", "created_at": datetime.now(UTC).isoformat(),
        "case_ids": case_ids, "repeats": args.repeats, "fixture_version": args.fixture_version,
        "judge_version": args.judge_version, "execution_mode": args.execution_mode,
        "fixture_manifest_hashes": fixture_hashes,
        "model_config": {
            "agent_model": agent_model or current_settings.siliconflow_agent_model,
            "judge_model": judge_model or current_settings.siliconflow_agent_model,
        },
        "serial_execution": True, "blind_executed": False,
    }
    atomic_json(args.output_dir / "benchmark_metadata.json", metadata)
    records: list[dict[str, Any]] = []
    jsonl = args.output_dir / "benchmark_runs.jsonl"
    for case_id in case_ids:
        case = case_by_id[case_id]
        for repeat in range(1, args.repeats + 1):
            run_id = f"{case_id.lower()}-r{repeat}-{uuid4().hex[:6]}"
            run_dir = args.output_dir / "runs" / run_id
            print(f"[{case_id} {repeat}/{args.repeats}]", flush=True)
            try:
                result = run_case(
                    case, source=source, run_dir=run_dir, agent_model=agent_model,
                    grade=True, judge_model=judge_model, max_rounds=args.max_tool_rounds,
                    pacing=args.pacing_seconds, judge_version=args.judge_version,
                    execution_mode=args.execution_mode,
                )
                result["execution_status"] = "CAPTURED"
                record = {"case_id": case_id, "repeat": repeat, "run_id": run_id,
                          "fixture_manifest_sha256": fixture_hashes[case_id], "result": result}
                record["metrics"] = _run_metrics(result)
            except Exception as exc:  # noqa: BLE001
                record = {"case_id": case_id, "repeat": repeat, "run_id": run_id,
                          "fixture_manifest_sha256": fixture_hashes[case_id],
                          "result": {"execution_status": "ERROR", "agent_status": "ERROR",
                                     "error": f"{type(exc).__name__}: {exc}"},
                          "metrics": {"agent_tokens": None, "tool_calls": 0, "quality_score_raw": None,
                                      "judge_outcome": None, "request_completed": False,
                                      "workflow_completed": False, "structural_pass": False,
                                      "judge_valid": False, "judge_contract_violation": False,
                                      "quality_pass": False, "strict_e2e_pass": False,
                                      "e2e_success": False,
                                      "final_outcome": "agent_business_execution_failure",
                                      "execution_outcome": "provider_error" if is_provider_exception(exc) else "recovery_failure",
                                      "judge_tokens": None,
                                      "critical_quality_failure": False}}
            records.append(record)
            with jsonl.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            atomic_json(args.output_dir / "benchmark_summary.json", {**metadata, **summarize(records)})

    fixed = next((row for row in records if row.get("result", {}).get("actual_output", {}).get("paper")), None)
    judge_stability = None
    if fixed:
        from evaluation.teaching_quality.judge.provider import build_siliconflow_judge_adapter
        settings = Settings()
        if judge_model:
            settings = settings.model_copy(update={"siliconflow_agent_model": judge_model})
        adapter = build_siliconflow_judge_adapter(settings, max_tokens=2048, pacing_seconds=args.pacing_seconds)
        grades = [grade_result(
            case_by_id[fixed["case_id"]], fixed["result"]["actual_output"], adapter,
            judge_version=args.judge_version,
        )
                  for _ in range(args.judge_stability_repeats)]
        scores = [(grade.get("score") or {}).get("weighted_score") for grade in grades]
        valid_scores = [score for score in scores if score is not None]
        judge_stability = {
            "case_id": fixed["case_id"], "source_run_id": fixed["run_id"],
            "paper_id": fixed["result"]["actual_output"]["paper"]["id"],
            "repeats": args.judge_stability_repeats, "scores": scores,
            "score_std": statistics.pstdev(valid_scores) if valid_scores else None,
            "axis_scores": [(grade.get("behavioral") or {}).get("scores") for grade in grades],
            "judge_tokens": sum(
                (((grade.get("judge_provider") or {}).get("last_call") or {}).get("usage") or {}).get("total_tokens", 0)
                for grade in grades
            ) or None,
            "grades": grades,
        }
        atomic_json(args.output_dir / "judge_stability.json", judge_stability)

    summary = {**metadata, **summarize(records), "judge_stability": judge_stability}
    atomic_json(args.output_dir / "benchmark_summary.json", summary)
    (args.output_dir / "benchmark_summary.md").write_text(_markdown(summary, judge_stability), encoding="utf-8")
    print(f"RUNS={jsonl}")
    print(f"SUMMARY={args.output_dir / 'benchmark_summary.json'}")


if __name__ == "__main__":
    main()
