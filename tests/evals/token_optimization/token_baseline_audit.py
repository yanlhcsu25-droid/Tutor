#!/usr/bin/env python3
"""Offline token-cost audit for saved Teacher Agent evaluation reports.

Reads JSON only, never calls a model or executes a business tool. Provider usage
is authoritative; character metrics are reported separately as context proxies.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

DEFAULT_SOURCE = Path(
    "tests/evals/reports/official-holdout-v3.2.1-69-second-20261006T045441Z.json"
)
DEFAULT_OUTPUT = Path(
    "tests/evals/token_optimization/reports/phase2_p0_cost_profile.json"
)
CONTEXT_FIELDS = (
    "system_prompt_chars",
    "tool_schema_chars",
    "conversation_chars",
    "workspace_chars",
    "observation_chars",
    "other_chars",
)


def _canonical_turns(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Use one trace representation; evaluated_turns is a duplicate projection."""
    return result.get("turns") or (result.get("actual") or {}).get("evaluated_turns") or []


def _spans(result: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        span
        for turn in _canonical_turns(result)
        for span in ((turn.get("observability") or {}).get(kind) or [])
        if isinstance(span, dict)
    ]


def _usage(span: dict[str, Any]) -> dict[str, Any]:
    output = span.get("output_json") or {}
    return {
        key: output.get(key) if isinstance(output.get(key), (int, float)) else None
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def _blocking_errors(result: dict[str, Any]) -> list[str]:
    actual = result.get("actual") or {}
    payloads = [actual.get("result"), actual.get("paper"), actual.get("generation")]
    found: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for error in payload.get("blocking_errors") or []:
            if str(error) not in found:
                found.append(str(error))
        preview = payload.get("generation_preview")
        if isinstance(preview, dict):
            for error in preview.get("blocking_errors") or []:
                if str(error) not in found:
                    found.append(str(error))
    return found


def _fingerprint(span: dict[str, Any]) -> str:
    input_json = span.get("input_json") or {}
    return json.dumps(
        [span.get("name"), input_json.get("arguments")],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _provider_attempts(span: dict[str, Any]) -> list[dict[str, Any]] | None:
    output = span.get("output_json") or {}
    attempts = output.get("provider_attempts")
    if attempts is None:
        attempts = (output.get("provider_metadata") or {}).get("provider_attempts")
    return attempts if isinstance(attempts, list) else None


def _current_tool_schemas() -> list[dict[str, Any]]:
    """Snapshot schemas without executing tools or opening the business database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools

    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        context = AgentExecutionContext(
            session=session,
            conversation_id=None,
            paper_id=None,
            version_id=None,
            state_store=None,
        )
        tools = build_agent_tools(context)
        return [
            {
                "name": name,
                "schema_chars": len(
                    json.dumps(
                        tool.definition(), ensure_ascii=False, separators=(",", ":")
                    )
                ),
            }
            for name, tool in tools.items()
        ]


def _duplicate_trace_summary(results: list[dict[str, Any]], kind: str) -> dict[str, int]:
    ids: list[str] = []
    for result in results:
        projections = [result.get("turns") or [], (result.get("actual") or {}).get("evaluated_turns") or []]
        for turns in projections:
            for turn in turns:
                for span in ((turn.get("observability") or {}).get(kind) or []):
                    identifier = span.get("span_id") or span.get("id")
                    if identifier:
                        ids.append(str(identifier))
    counts = Counter(ids)
    return {
        "records_across_known_projections": len(ids),
        "unique_span_ids": len(counts),
        "duplicate_projection_records": sum(count - 1 for count in counts.values()),
    }


def _summary(values: Iterable[float | int]) -> dict[str, float | int | None]:
    items = list(values)
    if not items:
        return {"sum": 0, "mean": None, "median": None, "max": None}
    return {
        "sum": sum(items),
        "mean": round(mean(items), 2),
        "median": round(median(items), 2),
        "max": max(items),
    }


def audit_report(payload: dict[str, Any], *, source_path: str, source_sha256: str) -> dict[str, Any]:
    results = payload.get("results") or []
    case_metrics: list[dict[str, Any]] = []
    model_spans: list[tuple[str, int, dict[str, Any]]] = []
    tool_spans: list[tuple[str, int, dict[str, Any]]] = []
    tool_names_by_category: dict[str, set[str]] = defaultdict(set)

    for result in results:
        case_id = str(result.get("case_id") or "unavailable")
        for turn_index, turn in enumerate(_canonical_turns(result), 1):
            observability = turn.get("observability") or {}
            model_spans.extend(
                (case_id, turn_index, span)
                for span in observability.get("model_calls") or []
            )
            for span in observability.get("tool_calls") or []:
                tool_spans.append((case_id, turn_index, span))
                if span.get("name"):
                    tool_names_by_category[str(result.get("category") or "unavailable")].add(
                        str(span["name"])
                    )

        case_metrics.append(
            {
                "case_id": case_id,
                "category": result.get("category"),
                "subtype": result.get("subtype"),
                "passed": result.get("passed"),
                "model_calls": result.get("model_calls"),
                "tool_calls": result.get("tool_calls"),
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
                "total_tokens": result.get("total_tokens"),
                "final_status": (result.get("actual") or {}).get("status"),
                "blocking_errors": _blocking_errors(result),
                "latency_ms": result.get("latency_ms"),
            }
        )

    exact_usage = {
        key: _summary(
            item[key]
            for item in case_metrics
            if isinstance(item.get(key), (int, float))
        )
        for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens", "latency_ms")
    }
    model_calling = [item for item in case_metrics if (item.get("model_calls") or 0) > 0]
    exact_usage["model_calling_case_count"] = len(model_calling)
    exact_usage["zero_model_call_case_count"] = len(case_metrics) - len(model_calling)
    exact_usage["mean_per_all_case"] = {
        key: round(exact_usage[key]["sum"] / len(case_metrics), 2) if case_metrics else None
        for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens", "latency_ms")
    }
    exact_usage["mean_per_model_calling_case"] = {
        key: round(mean(item[key] for item in model_calling), 2) if model_calling else None
        for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens", "latency_ms")
    }
    exact_usage["mean_per_model_call"] = {
        key: round(exact_usage[key]["sum"] / len(model_spans), 2) if model_spans else None
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }

    context_totals = Counter()
    workspace_totals = Counter()
    workspace_detail_totals: dict[str, Counter[str]] = defaultdict(Counter)
    valid_context_totals = Counter()
    valid_context_calls = 0
    reconciliation_failures: list[dict[str, Any]] = []
    round_metrics: dict[int, list[dict[str, Any]]] = defaultdict(list)
    definition_distribution: Counter[tuple[int, int]] = Counter()
    missing_context = 0
    missing_usage = 0
    for case_id, _, span in model_spans:
        usage = _usage(span)
        if any(usage[key] is None for key in usage):
            missing_usage += 1
        context = (span.get("input_json") or {}).get("context_metrics")
        if not isinstance(context, dict):
            missing_context += 1
            continue
        breakdown = context.get("context_breakdown") or {}
        context_totals.update({key: int(breakdown.get(key) or 0) for key in CONTEXT_FIELDS})
        workspace_totals.update(context.get("workspace_breakdown") or {})
        for section, values in (context.get("workspace_detail_breakdown") or {}).items():
            if isinstance(values, dict):
                workspace_detail_totals[str(section)].update(values)
        total_chars = context.get("total_chars")
        attributed = sum(int(breakdown.get(key) or 0) for key in CONTEXT_FIELDS)
        if total_chars == attributed:
            valid_context_totals.update(
                {key: int(breakdown.get(key) or 0) for key in CONTEXT_FIELDS}
            )
            valid_context_calls += 1
        else:
            reconciliation_failures.append(
                {
                    "case_id": case_id,
                    "span_id": span.get("span_id"),
                    "total_chars": total_chars,
                    "attributed_chars": attributed,
                    "difference": attributed - total_chars if isinstance(total_chars, int) else None,
                }
            )
        input_json = span.get("input_json") or {}
        round_metrics[int(input_json.get("tool_round") or 0)].append(
            {**{key: int(breakdown.get(key) or 0) for key in CONTEXT_FIELDS}, **usage}
        )
        n_definitions = input_json.get("n_definitions")
        schema_chars = context.get("tool_schema_chars")
        if isinstance(n_definitions, int) and isinstance(schema_chars, int):
            definition_distribution[(n_definitions, schema_chars)] += 1

    valid_chars = sum(valid_context_totals.values())
    context_proxy = {
        "unit": "characters_sent; not provider tokens",
        "calls_with_metrics": len(model_spans) - missing_context,
        "calls_missing_metrics": missing_context,
        "aggregate_chars_all_calls": dict(context_totals),
        "aggregate_workspace_chars_all_calls": dict(workspace_totals),
        "aggregate_workspace_detail_chars_all_calls": {
            section: dict(values) for section, values in workspace_detail_totals.items()
        },
        "reconciled_call_count": valid_context_calls,
        "reconciliation_failures": reconciliation_failures,
        "shares_on_reconciled_calls_pct": {
            key: round(100 * valid_context_totals[key] / valid_chars, 2) if valid_chars else None
            for key in CONTEXT_FIELDS
        },
        "by_tool_round": [
            {
                "tool_round": tool_round,
                "model_calls": len(items),
                "mean_input_tokens": round(
                    mean(item["input_tokens"] for item in items if item["input_tokens"] is not None),
                    2,
                ),
                **{
                    f"mean_{key}": round(mean(item[key] for item in items), 2)
                    for key in CONTEXT_FIELDS
                },
            }
            for tool_round, items in sorted(round_metrics.items())
        ],
    }

    repeated_tools: list[dict[str, Any]] = []
    failed_tool_continuations: list[dict[str, Any]] = []
    preview_continuations: list[dict[str, Any]] = []
    noop_rechecks: list[dict[str, Any]] = []
    for result in results:
        case_id = str(result.get("case_id") or "unavailable")
        for turn_index, turn in enumerate(_canonical_turns(result), 1):
            observability = turn.get("observability") or {}
            tools = observability.get("tool_calls") or []
            models = observability.get("model_calls") or []
            seen: set[str] = set()
            for tool in tools:
                fingerprint = _fingerprint(tool)
                if fingerprint in seen:
                    repeated_tools.append(
                        {"case_id": case_id, "turn": turn_index, "tool": tool.get("name")}
                    )
                seen.add(fingerprint)
                output = tool.get("output_json") or {}
                later = [m for m in models if str(m.get("started_at")) > str(tool.get("ended_at"))]
                if output.get("ok") is False and later:
                    failed_tool_continuations.append(
                        {"case_id": case_id, "turn": turn_index, "tool": tool.get("name"), "later_model_calls": len(later)}
                    )
                if tool.get("name") == "preview_paper_changes" and output.get("ok") is True and later:
                    preview_continuations.append(
                        {
                            "case_id": case_id,
                            "turn": turn_index,
                            "later_model_calls": len(later),
                            "later_input_tokens": sum((_usage(m)["input_tokens"] or 0) for m in later),
                            "later_output_tokens": sum((_usage(m)["output_tokens"] or 0) for m in later),
                        }
                    )
            for index, model in enumerate(models[:-1]):
                output = model.get("output_json") or {}
                if not output.get("tool_calls"):
                    usage = _usage(model)
                    noop_rechecks.append(
                        {
                            "case_id": case_id,
                            "turn": turn_index,
                            "model_call_index": index + 1,
                            **usage,
                        }
                    )

    attempts = [_provider_attempts(span) for _, _, span in model_spans]
    calls_with_attempts = [item for item in attempts if item is not None]
    tool_output_chars: dict[str, list[int]] = defaultdict(list)
    for _, _, span in tool_spans:
        tool_output_chars[str(span.get("name") or "unavailable")].append(
            len(
                json.dumps(
                    span.get("output_json") or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        )
    current_schemas = _current_tool_schemas()
    current_schema_map = {item["name"]: item["schema_chars"] for item in current_schemas}
    task_schema_scenarios = []
    for category in sorted({str(item.get("category") or "unavailable") for item in case_metrics}):
        used = sorted(tool_names_by_category[category])
        retained = sum(current_schema_map.get(name, 0) for name in used)
        total = sum(current_schema_map.values())
        task_schema_scenarios.append(
            {
                "category": category,
                "actually_used_tools_in_historical_trace": used,
                "current_registry_tools_not_used_by_category": sorted(set(current_schema_map) - set(used)),
                "current_all_schema_chars": total,
                "used_only_schema_chars": retained,
                "theoretical_removed_chars": total - retained,
                "caveat": "Current-schema scenario only; actual required tools and historical exposed names are unavailable.",
            }
        )

    category_by_case = {
        str(result.get("case_id") or "unavailable"): str(
            result.get("category") or "unavailable"
        )
        for result in results
    }
    historical_exposure_by_category = []
    for category in sorted(set(category_by_case.values())):
        spans = [
            span
            for case_id, _, span in model_spans
            if category_by_case[case_id] == category
        ]
        counts = [
            (span.get("input_json") or {}).get("n_definitions")
            for span in spans
            if isinstance((span.get("input_json") or {}).get("n_definitions"), int)
        ]
        chars = [
            ((span.get("input_json") or {}).get("context_metrics") or {}).get(
                "tool_schema_chars"
            )
            for span in spans
            if isinstance(
                ((span.get("input_json") or {}).get("context_metrics") or {}).get(
                    "tool_schema_chars"
                ),
                int,
            )
        ]
        historical_exposure_by_category.append(
            {
                "category": category,
                "model_calls": len(spans),
                "mean_exposed_tool_count": round(mean(counts), 2) if counts else None,
                "mean_tool_schema_chars": round(mean(chars), 2) if chars else None,
                "actually_used_tools": sorted(tool_names_by_category[category]),
            }
        )

    return {
        "audit_version": "phase2-p0-v1",
        "source": {
            "path": source_path,
            "sha256": source_sha256,
            "generated_at": payload.get("generated_at"),
            "metadata": payload.get("metadata"),
        },
        "data_contract": {
            "exact_token_source": "provider usage stored per model span and rolled up per case",
            "context_proxy_source": "runtime character counters; never converted to exact tokens",
            "canonical_trace_path": "results[].turns[].observability",
            "historical_exposed_tool_names": "unavailable; only count and aggregate schema chars were stored",
            "provider_attempt_detail": "available only when provider_attempts exists in a model span",
        },
        "overview": {
            "cases": len(case_metrics),
            "passed": sum(item.get("passed") is True for item in case_metrics),
            **exact_usage,
            "model_call_distribution": dict(sorted(Counter(item.get("model_calls") or 0 for item in case_metrics).items())),
            "final_status_distribution": dict(sorted(Counter(str(item.get("final_status")) for item in case_metrics).items())),
            "model_spans": len(model_spans),
            "tool_spans": len(tool_spans),
            "model_spans_missing_provider_usage": missing_usage,
        },
        "case_metrics": sorted(case_metrics, key=lambda item: item["case_id"]),
        "top_10_by_total_tokens": sorted(case_metrics, key=lambda item: item.get("total_tokens") or 0, reverse=True)[:10],
        "context_size_proxy": context_proxy,
        "tool_schema": {
            "historical_exposure_distribution": [
                {"exposed_tool_count": count, "tool_schema_chars": chars, "model_calls": occurrences}
                for (count, chars), occurrences in sorted(definition_distribution.items())
            ],
            "current_registry_tool_count": len(current_schemas),
            "current_registry_total_schema_chars": sum(item["schema_chars"] for item in current_schemas),
            "current_registry_schemas": current_schemas,
            "historical_tool_usage": dict(sorted(Counter(span.get("name") for _, _, span in tool_spans).items())),
            "historical_exposure_by_category": historical_exposure_by_category,
            "task_scenarios": task_schema_scenarios,
        },
        "tool_observation_proxy": {
            "model_visible_observation_chars": context_totals["observation_chars"],
            "saved_tool_output_json_chars_by_tool": {
                name: _summary(values) for name, values in sorted(tool_output_chars.items())
            },
            "caveat": "Saved output JSON length is a size proxy and may differ from serialized model-visible observation text.",
        },
        "loop_audit": {
            "exact_repeated_tool_calls_same_turn": repeated_tools,
            "failed_tool_then_model_continuations": failed_tool_continuations,
            "successful_preview_then_model_continuations": preview_continuations,
            "intermediate_no_tool_output_then_recheck": {
                "count": len(noop_rechecks),
                "input_tokens": sum(item.get("input_tokens") or 0 for item in noop_rechecks),
                "output_tokens": sum(item.get("output_tokens") or 0 for item in noop_rechecks),
                "records": noop_rechecks,
                "classification": "historical optimization candidates, not proof every call was unnecessary",
            },
        },
        "provider_attempts": {
            "calls_with_attempt_detail": len(calls_with_attempts),
            "calls_without_attempt_detail": len(model_spans) - len(calls_with_attempts),
            "attempt_records": sum(len(item) for item in calls_with_attempts),
            "retry_attempts": sum(max(0, len(item) - 1) for item in calls_with_attempts),
            "retry_cost_attribution": "available" if calls_with_attempts else "unavailable",
        },
        "duplicate_trace_detection": {
            "model_calls": _duplicate_trace_summary(results, "model_calls"),
            "tool_calls": _duplicate_trace_summary(results, "tool_calls"),
            "note": "Duplicate projection records are storage copies, not additional executions.",
        },
    }


def run(source: Path, output: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    profile = audit_report(
        json.loads(raw), source_path=str(source), source_sha256=sha256(raw).hexdigest()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    profile = run(args.source, args.output)
    overview = profile["overview"]
    print(
        f"audited {overview['cases']} cases, {overview['model_calls']['sum']} model calls, "
        f"{overview['total_tokens']['sum']} provider tokens -> {args.output}"
    )


if __name__ == "__main__":
    main()
