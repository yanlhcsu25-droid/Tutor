"""Small boundary suite for deterministic precedence and live semantic routing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

from calculus_agent.agent.semantic_router import resolve_semantic_task_route
from calculus_agent.agent.task_router import (
    RoutingState,
    TaskType,
    classify_message,
    deterministic_route,
)
from calculus_agent.config import get_settings
from calculus_agent.runtime.coordinator import build_teacher_agent_backend


CASE_FILE = Path(__file__).parent / "cases" / "task_routing_boundary_v0.yaml"
CASE_FILE_SHA256 = "0ac2e94809c645a63c6327840ccefb7ac09bd54871a22b497584f1a8da0ca7f8"


def _cases() -> list[dict]:
    return yaml.safe_load(CASE_FILE.read_text(encoding="utf-8"))["cases"]


def _state(case: dict) -> RoutingState:
    return RoutingState(**(case.get("state") or {}))


def test_boundary_suite_keeps_semantics_out_of_deterministic_overrides() -> None:
    cases = _cases()
    assert hashlib.sha256(CASE_FILE.read_bytes()).hexdigest() == CASE_FILE_SHA256
    assert len(cases) == 9

    for case in cases:
        decision = deterministic_route(case["text"], state=_state(case))
        if case["id"] == "ROUTE-B08":
            assert decision is not None
            assert decision.route.task_type == TaskType.DIRECT_ACTION
        elif case.get("expected_source") == "deterministic_state":
            assert decision is not None
            assert decision.source == "deterministic_state"
        else:
            assert decision is None, case["id"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="set RUN_LIVE_LLM=1 to run live semantic routing",
)
def test_live_semantic_routing_boundary() -> None:
    backend = build_teacher_agent_backend(get_settings())
    assert backend is not None, "live semantic routing requires a configured API key"

    failures = []
    for case in _cases():
        deterministic = deterministic_route(case["text"], state=_state(case))
        route = deterministic.route if deterministic else resolve_semantic_task_route(
            case["text"], backend=backend
        )
        route = route or classify_message(case["text"])
        clarification_required = bool(case.get("clarification_required"))
        allowed = set(case.get("allowed") or [case["expected"]])
        if not clarification_required and route.task_type.value not in allowed:
            failures.append((case["id"], route.task_type.value))
        if clarification_required and not route.clarification_needed:
            failures.append((case["id"], "clarification not requested"))

    assert not failures, repr(failures)
