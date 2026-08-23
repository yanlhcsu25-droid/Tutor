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


def summarize(report: dict[str, Any], *, variant: str) -> dict[str, Any]:
    """Summarize observable outcomes without re-grading model prose."""
    results = report.get("results") or []
    count = len(results)
    passed = sum(bool(item.get("passed")) for item in results)
    false_successes = sum(
        not item.get("passed")
        and (item.get("actual") or {}).get("status") == "completed"
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
    tool_counts = [len(_tool_calls(item)) for item in results]

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
        "average_tool_calls": round(mean(tool_counts), 2) if tool_counts else 0.0,
        "total_tool_calls": sum(tool_counts),
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
            display(summary[key], key) for _, key in columns
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
