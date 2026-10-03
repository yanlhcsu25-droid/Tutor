"""Build a compact, reproducible Agent reliability report from eval output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _graders(result: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [
        grader for grader in result.get("graders", [])
        if grader.get("grader") == name
    ]


def _tool_calls(result: dict[str, Any]) -> list[dict[str, Any]]:
    return ((result.get("actual") or {}).get("trace") or {}).get("tool_calls") or []


def _numbers(results: list[dict[str, Any]], field: str) -> list[float]:
    return [
        float(item[field]) for item in results
        if isinstance(item.get(field), (int, float)) and not isinstance(item.get(field), bool)
    ]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _average(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _eligible_rate(results: list[dict[str, Any]], field: str) -> float | None:
    values = [item[field] for item in results if isinstance(item.get(field), bool)]
    return round(sum(values) / len(values), 4) if values else None


def _group_metrics(results: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        if field == "turn_type":
            key = "multi-turn" if int(item.get("total_turns") or len(item.get("turns") or [])) > 1 else "single-turn"
        else:
            key = str(item.get(field) or "unspecified")
        groups.setdefault(key, []).append(item)
    return {
        key: {
            "cases": len(items),
            "task_success_rate": round(
                sum(bool(item.get("task_success", item.get("passed"))) for item in items) / len(items), 4,
            ),
            "average_total_tokens": _average(_numbers(items, "total_tokens")),
            "average_latency_ms": _average(_numbers(items, "latency_ms")),
        }
        for key, items in sorted(groups.items())
    }


def summarize(report: dict[str, Any], *, variant: str) -> dict[str, Any]:
    """Summarize observable outcomes without re-grading model prose."""
    results = report.get("results") or []
    count = len(results)
    passed = sum(bool(item.get("passed")) for item in results)
    false_successes = sum(
        bool(item.get("false_success"))
        if isinstance(item.get("false_success"), bool)
        else (
            not item.get("passed")
            and (item.get("actual") or {}).get("status") == "completed"
        )
        for item in results
    )
    state_graders = [grader for item in results for grader in _graders(item, "state")]
    confirmation_cases = [
        item for item in results
        if (item.get("expected") or {}).get("status") == "waiting_confirmation"
        or any(
            (grader.get("details") or {}).get("status") == "waiting_confirmation"
            for grader in _graders(item, "acceptance")
        )
    ]
    recovery_cases = [
        item for item in results
        if item.get("category") == "error_handling"
        or (item.get("expected") or {}).get("status") == "needs_clarification"
    ]
    grounding_cases = [
        item for item in results
        if item.get("category") == "paper_modification"
    ]
    tool_counts = [
        int(item.get("tool_calls"))
        if isinstance(item.get("tool_calls"), int)
        else len(_tool_calls(item))
        for item in results
    ]
    model_calls = _numbers(results, "model_calls")
    total_turns = _numbers(results, "total_turns")
    latencies = _numbers(results, "latency_ms")
    input_tokens = _numbers(results, "input_tokens")
    output_tokens = _numbers(results, "output_tokens")
    total_tokens = _numbers(results, "total_tokens")
    successful = [item for item in results if item.get("task_success", item.get("passed"))]
    successful_count = len(successful)

    def pass_rate(items: list[dict[str, Any]]) -> float | None:
        return round(sum(bool(item.get("passed")) for item in items) / len(items), 4) if items else None

    case_details = []
    for item in results:
        grader_errors = [
            error
            for grader in item.get("graders", [])
            for error in (grader.get("errors") or [])
        ]
        runner_error = item.get("error") or {}
        if runner_error.get("message"):
            grader_errors.append(str(runner_error["message"]))
        actual = item.get("actual") or {}
        observed_errors = []
        if (actual.get("error") or {}).get("code"):
            observed_errors.append(str(actual["error"]["code"]))
        for call in ((actual.get("trace") or {}).get("tool_calls") or []):
            result = call.get("result") or {}
            if isinstance(result, dict) and result.get("ok") is False and result.get("code"):
                code = str(result["code"])
                if code not in observed_errors:
                    observed_errors.append(code)
        case_details.append({
            "case_id": item.get("case_id"),
            "category": item.get("category"),
            "passed": bool(item.get("passed")),
            "status": actual.get("status"),
            "observed_errors": observed_errors,
            "failure_reasons": grader_errors,
        })

    return {
        "variant": variant,
        "metadata": {
            **(report.get("metadata") or {}),
            "generated_at": report.get("generated_at"),
        },
        "cases": case_details,
        "case_count": count,
        "task_success_rate": round(passed / count, 4) if count else 0.0,
        "false_success_rate": round(false_successes / count, 4) if count else 0.0,
        "confirmation_safety_rate": pass_rate(confirmation_cases),
        "recovery_rate": pass_rate(recovery_cases),
        "grounding_rate": pass_rate(grounding_cases),
        "state_consistency_rate": (
            round(sum(bool(item.get("passed")) for item in state_graders) / len(state_graders), 4)
            if state_graders else None
        ),
        "constraint_preservation_rate": _eligible_rate(results, "constraint_preserved"),
        "clarification_accuracy": _eligible_rate(results, "clarification_correct"),
        "side_effect_violation_rate": _eligible_rate(results, "side_effect_violation"),
        "grounded_answer_accuracy": _eligible_rate(results, "grounding_correct"),
        "average_latency_ms": _average(latencies),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "average_tool_calls": round(mean(tool_counts), 2) if tool_counts else 0.0,
        "total_tool_calls": sum(tool_counts),
        "average_model_calls": _average(model_calls),
        "average_turns": _average(total_turns),
        "average_input_tokens": _average(input_tokens),
        "average_output_tokens": _average(output_tokens),
        "average_total_tokens": _average(total_tokens),
        "p50_total_tokens": _percentile(total_tokens, 0.50),
        "p95_total_tokens": _percentile(total_tokens, 0.95),
        "tokens_per_successful_task": (
            round(sum(total_tokens) / successful_count, 2)
            if successful_count and len(total_tokens) == count else None
        ),
        "token_usage_coverage": round(len(total_tokens) / count, 4) if count else 0.0,
        "breakdowns": {
            "category": _group_metrics(results, "category"),
            "difficulty": _group_metrics(results, "difficulty"),
            "turn_type": _group_metrics(results, "turn_type"),
        },
    }


def markdown(summaries: list[dict[str, Any]]) -> str:
    columns = [
        ("Variant", "variant"), ("Cases", "case_count"),
        ("Task Success", "task_success_rate"),
        ("False Success", "false_success_rate"),
        ("Confirmation", "confirmation_safety_rate"),
        ("Recovery", "recovery_rate"), ("Grounding", "grounding_rate"),
        ("State", "state_consistency_rate"),
        ("Avg Tools", "average_tool_calls"),
        ("Avg Tokens", "average_total_tokens"),
        ("P95 Latency", "p95_latency_ms"),
    ]

    def display(value: Any, key: str) -> str:
        if value is None:
            return "N/A"
        if key.endswith("_rate"):
            return f"{value * 100:.1f}%"
        return str(value)

    case_counts = sorted({summary["case_count"] for summary in summaries})
    count_label = "/".join(str(count) for count in case_counts)
    failure_only = all("failure-injection" in summary["variant"] for summary in summaries)
    lines = [
        "# Teacher Agent Reliability Benchmark",
        "",
        "All displayed variants use the same case inputs, fixtures, model temperature, and "
        "graders. Missing variants are omitted rather than estimated.",
        "",
        f"> Limitations: this is a single run over {count_label} cases. The cases and "
        "graders remain implementation-adjacent, so the result is a reproducible regression "
        "benchmark, not evidence of reliability on unknown scenarios.",
        "",
    ]
    if not failure_only:
        lines.extend([
            "The deterministic failure-injection run is reported separately in "
            "[agent_reliability_failure_injection.md](agent_reliability_failure_injection.md).",
            "",
        ])
    lines.extend([
        "| " + " | ".join(label for label, _ in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ])
    for summary in summaries:
        lines.append("| " + " | ".join(
            display(summary.get(key), key) for _, key in columns
        ) + " |")

    for summary in summaries:
        metadata = summary.get("metadata") or {}
        if metadata:
            lines.extend([
                "", f"## Run metadata: {summary['variant']}", "",
                f"- Run time: `{metadata.get('generated_at') or 'N/A'}`",
                f"- Git SHA: `{metadata.get('git_sha') or 'N/A'}`",
                f"- Git dirty: `{metadata.get('git_dirty', 'N/A')}`",
                f"- Model ID: `{', '.join(metadata.get('model_ids') or []) or 'N/A'}`",
                f"- Temperature: `{metadata.get('temperature', 'N/A')}`",
                f"- Dataset: `{metadata.get('dataset_path') or 'N/A'}`",
                f"- Dataset version: `{metadata.get('dataset_version') or 'N/A'}`",
            ])
        lines.extend([
            "", f"## Efficiency and cost: {summary['variant']}", "",
            f"- Latency avg / P50 / P95: `{summary.get('average_latency_ms')}` / "
            f"`{summary.get('p50_latency_ms')}` / `{summary.get('p95_latency_ms')}` ms",
            f"- Average model calls / tool calls / turns: "
            f"`{summary.get('average_model_calls')}` / `{summary.get('average_tool_calls')}` / "
            f"`{summary.get('average_turns')}`",
            f"- Tokens avg input / output / total: `{summary.get('average_input_tokens')}` / "
            f"`{summary.get('average_output_tokens')}` / `{summary.get('average_total_tokens')}`",
            f"- Total tokens P50 / P95: `{summary.get('p50_total_tokens')}` / "
            f"`{summary.get('p95_total_tokens')}`",
            f"- Tokens per successful task: `{summary.get('tokens_per_successful_task')}`",
            f"- Actual token coverage: `{summary.get('token_usage_coverage')}`",
        ])
        for breakdown_name, groups in (summary.get("breakdowns") or {}).items():
            lines.extend([
                "", f"### By {breakdown_name}", "",
                "| Group | Cases | Task Success | Avg Tokens | Avg Latency |",
                "| --- | ---: | ---: | ---: | ---: |",
            ])
            for group, metrics in groups.items():
                lines.append(
                    f"| {group} | {metrics['cases']} | "
                    f"{display(metrics['task_success_rate'], 'task_success_rate')} | "
                    f"{display(metrics['average_total_tokens'], 'average_total_tokens')} | "
                    f"{display(metrics['average_latency_ms'], 'average_latency_ms')} |"
                )
        cases = summary.get("cases") or []
        if cases:
            lines.extend([
                "", f"## Case details: {summary['variant']}", "",
                "| Case | Category | Status | Result | Observed error | Failure reason |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            for case in cases:
                reasons = ("; ".join(case.get("failure_reasons") or []) or "—").replace(
                    "|", "\\|"
                )
                observed = ", ".join(case.get("observed_errors") or []) or "—"
                lines.append(
                    f"| {case.get('case_id')} | {case.get('category')} | "
                    f"{case.get('status')} | {'PASS' if case.get('passed') else 'FAIL'} | "
                    f"{observed} | {reasons} |"
                )
    lines.extend([
        "", "## Metric definitions", "",
        "- **Task Success:** case-level acceptance pass rate.",
        "- **False Success:** failed cases whose final status was `completed`.",
        "- **Confirmation:** pass rate for cases expected to await confirmation.",
        "- **Recovery:** pass rate for error-handling or clarification cases.",
        "- **Grounding:** pass rate for paper-modification cases.",
        "- **State:** state-grader pass rate.",
        "- **Avg Tools:** mean observed Tool calls per case.",
        "- **Avg Tokens:** mean actual provider tokens; unavailable usage is excluded, never zero-filled.",
        "- **P95 Latency:** interpolated 95th percentile of case wall-clock latency.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize Teacher Agent reliability")
    parser.add_argument(
        "report", nargs="+", type=Path,
        help="variant=path/to/report.json (or just path for state-policy)",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    summaries = []
    for item in args.report:
        raw = str(item)
        variant, separator, path = raw.partition("=")
        if not separator:
            variant, path = "state-policy", raw
        summaries.append(summarize(
            json.loads(Path(path).read_text(encoding="utf-8")), variant=variant,
        ))
    payload = {"variants": summaries}
    rendered = markdown(summaries)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(rendered, encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        print(rendered, end="")


if __name__ == "__main__":
    main()
