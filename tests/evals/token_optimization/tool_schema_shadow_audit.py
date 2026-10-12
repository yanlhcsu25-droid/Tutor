#!/usr/bin/env python3
"""Offline shadow audit for feature-gated Dynamic Tool Selection."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from calculus_agent.agent.task_router import (
    RoutingState,
    TaskType,
    decide_task,
    has_explicit_curriculum_scope,
    requires_teaching_design_artifact,
)
from calculus_agent.agent.tool_adapters.teaching_environment import (
    environment_inspection_tool_names,
)
from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.agent.toolkit import Toolkit
from calculus_agent.runtime.tool_exposure_policy import ToolExposureContext, ToolExposurePolicy
from calculus_agent.runtime.tool_selection import compare_tool_exposure, task_chain_tools

DEFAULT_SOURCE = Path(
    "tests/evals/reports/official-holdout-v3.2.1-69-second-20261006T045441Z.json"
)
DEFAULT_OUTPUT = Path(
    "tests/evals/token_optimization/reports/phase2_p1_tool_schema_shadow_audit.json"
)


def _has_rows(state: dict[str, Any], section: str, table: str) -> bool:
    values = (state.get(section) or {}).get(table) or []
    return bool(values)


def _routing_state(state: dict[str, Any]) -> RoutingState:
    return RoutingState(
        pending_generation=_has_rows(state, "pending", "agent_pending_generation"),
        pending_paper_change=_has_rows(state, "pending", "agent_pending_adjustment"),
        pending_replacement=_has_rows(state, "pending", "agent_pending_replacement"),
        current_paper=_has_rows(state, "paper", "paper"),
        active_teaching_design=_has_rows(
            state, "teaching_design", "active_teaching_design"
        ),
    )


def _design_names(state: RoutingState) -> tuple[str, ...]:
    if not state.active_teaching_design:
        return ()
    return (
        "read_active_teaching_design",
        "revise_teaching_design",
        "confirm_teaching_design",
        "discard_teaching_design",
    )


def _toolkit() -> tuple[Toolkit, frozenset[str]]:
    engine = create_engine("sqlite:///:memory:")
    session = Session(engine)
    tools = build_agent_tools(AgentExecutionContext(
        session=session,
        conversation_id=None,
        paper_id=None,
        version_id=None,
        state_store=None,
    ))
    # Closures retain the session, but the shadow audit only reads definitions.
    return Toolkit(tools.values()), frozenset(tools)


def _policy_scenarios(toolkit: Toolkit, registered: frozenset[str]) -> list[dict[str, Any]]:
    common = dict(
        tool_names=registered,
        pending_generation=False,
        pending_paper_change=False,
        pending_replacement=False,
        legacy_teaching_design_active=False,
        design_tool_names=(),
        environment_tool_names=tuple(environment_inspection_tool_names()),
        pending_teaching_design_action=None,
        teaching_design_artifact_requested=False,
        has_explicit_curriculum_scope=False,
        route_source="deterministic_operation",
        route_confidence=1.0,
        clarification_needed=False,
        route_reason="",
    )
    scenarios = {
        "READ_CURRENT_PAPER": dict(
            task_type="INFORMATION_REQUEST", message="查看当前试卷有多少题",
            has_current_paper=True,
        ),
        "GENERATION_NEW": dict(
            task_type="DIRECT_ACTION", message="第一章生成10题100分测试卷",
            has_current_paper=False, route_reason="explicit generation or paper action wording",
        ),
        "PAPER_CHANGE_PREVIEW": dict(
            task_type="DIRECT_ACTION", message="把第3题换成更难的题",
            has_current_paper=True, route_reason="current paper operation wording under active paper context",
        ),
        "TEACHING_DESIGN": dict(
            task_type="TEACHING_DESIGN", message="为第一章设计复习方案",
            has_current_paper=False, teaching_design_artifact_requested=True,
            has_explicit_curriculum_scope=True,
        ),
        "MIXED": dict(
            task_type="DIRECT_ACTION", message="删除第2题并生成一套新练习卷",
            has_current_paper=True, route_reason="current paper operation wording under active paper context",
        ),
        "PENDING_GENERATION_CONFIRM": dict(
            task_type="DIRECT_ACTION", message="确认，就按这个生成",
            has_current_paper=False, pending_generation=True,
        ),
        "PENDING_PAPER_CHANGE_CONFIRM": dict(
            task_type="DIRECT_ACTION", message="确认，就按这个",
            has_current_paper=True, pending_paper_change=True,
        ),
    }
    records = []
    for name, changes in scenarios.items():
        values = {**common, **changes}
        values["task_type"] = TaskType[values["task_type"]]
        context = ToolExposureContext(**values)
        full_names = ToolExposurePolicy(dynamic_enabled=False).initial_tools(context)
        dynamic = ToolExposurePolicy(dynamic_enabled=True)
        dynamic.initial_tools(context)
        comparison = compare_tool_exposure(toolkit, full_names, dynamic.last_selection)
        records.append({"scenario": name, **comparison})
    return records


def audit(payload: dict[str, Any], *, source_path: str, source_sha256: str) -> dict[str, Any]:
    toolkit, registered = _toolkit()
    records: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []

    for result in payload.get("results") or []:
        case_id = str(result.get("case_id") or "unavailable")
        case_passed = result.get("passed") is True
        for turn in result.get("turns") or []:
            message = str(turn.get("user") or "")
            before = turn.get("business_state_before") or {}
            state = _routing_state(before)
            task = decide_task(message, state=state)
            context = ToolExposureContext(
                tool_names=registered,
                task_type=task.route.task_type,
                message=message,
                has_current_paper=state.current_paper,
                pending_generation=state.pending_generation,
                pending_paper_change=state.pending_paper_change,
                pending_replacement=state.pending_replacement,
                legacy_teaching_design_active=state.active_teaching_design,
                design_tool_names=_design_names(state),
                environment_tool_names=tuple(environment_inspection_tool_names()),
                pending_teaching_design_action=None,
                teaching_design_artifact_requested=requires_teaching_design_artifact(
                    message
                ),
                has_explicit_curriculum_scope=has_explicit_curriculum_scope(message),
                route_source=task.source,
                route_confidence=task.route.confidence,
                clarification_needed=task.route.clarification_needed,
                route_reason=task.route.reason,
            )
            phase1_policy = ToolExposurePolicy(dynamic_enabled=False)
            full_names = phase1_policy.initial_tools(context)
            dynamic_policy = ToolExposurePolicy(dynamic_enabled=True)
            dynamic_policy.initial_tools(context)
            decision = dynamic_policy.last_selection
            comparison = compare_tool_exposure(toolkit, full_names, decision)
            chain = set(task_chain_tools(decision, registered))
            tool_spans = (turn.get("observability") or {}).get("tool_calls") or []
            required = sorted({
                str(span.get("name"))
                for span in tool_spans
                if span.get("name") and case_passed
            })
            missing = sorted(set(required) - chain)
            model_calls = len(
                (turn.get("observability") or {}).get("model_calls") or []
            )
            record = {
                "case_id": case_id,
                "turn": turn.get("turn"),
                "case_passed": case_passed,
                "reconstructed_task_type": task.route.task_type.value,
                "reconstructed_route_source": task.source,
                "task_family": decision.task_family,
                "reason": decision.reason,
                "fallback": decision.fallback,
                "model_calls": model_calls,
                "historical_required_tools": required,
                "allowed_multi_round_chain": sorted(chain),
                "missing_required_tools": missing,
                **{
                    key: comparison[key]
                    for key in (
                        "full_tool_count", "selected_tool_count", "full_schema_chars",
                        "selected_schema_chars", "schema_chars_reduction",
                    )
                },
            }
            records.append(record)
            if missing:
                unsafe.append({
                    "case_id": case_id,
                    "turn": turn.get("turn"),
                    "missing_required_tools": missing,
                })

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["task_family"]].append(record)

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        calls = sum(item["model_calls"] for item in items)
        required = sum(len(item["historical_required_tools"]) for item in items)
        missing = sum(len(item["missing_required_tools"]) for item in items)
        calling_items = [item for item in items if item["model_calls"] > 0]
        weight = sum(item["model_calls"] for item in calling_items)

        def weighted(key: str) -> float:
            if weight:
                return round(
                    sum(item[key] * item["model_calls"] for item in calling_items)
                    / weight,
                    2,
                )
            return round(sum(item[key] for item in items) / (len(items) or 1), 2)
        full_chars = sum(
            item["full_schema_chars"] * item["model_calls"] for item in items
        )
        selected_chars = sum(
            item["selected_schema_chars"] * item["model_calls"] for item in items
        )
        return {
            "turns": len(items),
            "model_calls": calls,
            "mean_full_tool_count": weighted("full_tool_count"),
            "mean_selected_tool_count": weighted("selected_tool_count"),
            "weighted_full_schema_chars": full_chars,
            "weighted_selected_schema_chars": selected_chars,
            "weighted_schema_chars_reduction": full_chars - selected_chars,
            "weighted_schema_chars_reduction_rate": round(
                (full_chars - selected_chars) / full_chars, 4
            ) if full_chars else 0,
            "fallback_turns": sum(item["fallback"] for item in items),
            "fallback_rate": round(
                sum(item["fallback"] for item in items) / len(items), 4
            ) if items else 0,
            "fallback_model_calls": sum(
                item["model_calls"] for item in items if item["fallback"]
            ),
            "fallback_model_call_rate": round(
                sum(item["model_calls"] for item in items if item["fallback"]) / calls,
                4,
            ) if calls else 0,
            "unsafe_pruning_cases": sum(bool(item["missing_required_tools"]) for item in items),
            "historical_required_tool_coverage": round(
                (required - missing) / required, 4
            ) if required else 1.0,
        }

    all_summary = summarize(records)
    return {
        "audit_version": "phase2-p1-shadow-v1",
        "source": {
            "path": source_path,
            "sha256": source_sha256,
            "historical_git_sha": (payload.get("metadata") or {}).get("git_sha"),
        },
        "method": {
            "model_calls": 0,
            "database_writes": 0,
            "original_task_route": "unavailable in historical report",
            "route_method": "reconstructed with current deterministic/heuristic TaskRouter",
            "schema_comparison": "current Phase-1 surface versus feature-enabled selection",
            "weighting": "selected initial schema repeated by historical model-call count per turn",
            "limitation": "Stage-specific historical exposed names are absent; coverage uses the policy's conservative multi-round chain.",
        },
        "overall": all_summary,
        "by_task_family": {
            family: summarize(items) for family, items in sorted(grouped.items())
        },
        "policy_scenarios": _policy_scenarios(toolkit, registered),
        "unsafe_pruning_cases": unsafe,
        "turns": records,
    }


def run(source: Path, output: Path) -> dict[str, Any]:
    raw = source.read_bytes()
    report = audit(
        json.loads(raw), source_path=str(source), source_sha256=sha256(raw).hexdigest()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.source, args.output)
    overall = report["overall"]
    print(
        f"shadow audited {overall['turns']} turns; coverage="
        f"{overall['historical_required_tool_coverage']:.2%}; "
        f"unsafe={overall['unsafe_pruning_cases']} -> {args.output}"
    )


if __name__ == "__main__":
    main()
