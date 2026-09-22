"""Versioned 100-case validation set for top-level Teacher Agent routing."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import yaml

from calculus_agent.agent.task_router import RoutingState, decide_task


CASE_FILE = Path(__file__).parent / "cases" / "task_intent_validation_v0.yaml"
CASE_FILE_SHA256 = "9f8d326420471961f25b1afd6e7499caf03eba50c23b0af47b7b9fd0034b351a"
LABELS = {
    "DIRECT_ACTION",
    "TEACHING_DESIGN",
    "TEACHING_PLANNING",
    "INFORMATION_REQUEST",
}


def _load_cases() -> list[dict]:
    payload = yaml.safe_load(CASE_FILE.read_text(encoding="utf-8"))
    return payload["cases"]


def _state(raw: dict | None) -> RoutingState:
    raw = raw or {}
    return RoutingState(**{
        field: bool(raw.get(field))
        for field in RoutingState.model_fields
    })


def _metrics(cases: list[dict]) -> dict:
    confusion: dict[str, Counter] = defaultdict(Counter)
    mistakes: list[dict] = []
    source_mistakes: list[dict] = []
    for case in cases:
        decision = decide_task(case["text"], state=_state(case.get("state")))
        actual = decision.route.task_type.value
        expected = case["expected"]
        confusion[expected][actual] += 1
        if actual != expected:
            mistakes.append({
                "id": case["id"],
                "text": case["text"],
                "expected": expected,
                "actual": actual,
            })
        expected_source = case.get("expected_source")
        if expected_source is not None and decision.source != expected_source:
            source_mistakes.append({
                "id": case["id"],
                "expected": expected_source,
                "actual": decision.source,
            })

    per_label: dict[str, dict[str, float]] = {}
    for label in sorted(LABELS):
        true_positive = confusion[label][label]
        false_negative = sum(confusion[label].values()) - true_positive
        false_positive = sum(
            row[label] for expected, row in confusion.items() if expected != label
        )
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        per_label[label] = {"precision": precision, "recall": recall, "f1": f1}

    return {
        "accuracy": (len(cases) - len(mistakes)) / len(cases),
        "macro_f1": sum(item["f1"] for item in per_label.values()) / len(per_label),
        "per_label": per_label,
        "confusion": {label: dict(confusion[label]) for label in sorted(LABELS)},
        "mistakes": mistakes,
        "source_mistakes": source_mistakes,
    }


def test_intent_validation_dataset_is_balanced_and_context_explicit() -> None:
    cases = _load_cases()

    assert hashlib.sha256(CASE_FILE.read_bytes()).hexdigest() == CASE_FILE_SHA256
    assert len(cases) == 100
    assert len({case["id"] for case in cases}) == 100
    assert len({case["text"] for case in cases}) == 100
    assert Counter(case["expected"] for case in cases) == Counter({label: 25 for label in LABELS})
    state_fields = {
        field
        for case in cases
        for field in (case.get("state") or {})
    }
    assert state_fields == {
        "pending_generation",
        "pending_paper_change",
        "pending_replacement",
        "current_paper",
        "active_teaching_design",
    }


def test_task_router_meets_validation_quality_gate() -> None:
    metrics = _metrics(_load_cases())

    assert metrics["accuracy"] >= 0.90, metrics
    assert metrics["macro_f1"] >= 0.90, metrics
    assert all(
        item["recall"] >= 0.80
        for item in metrics["per_label"].values()
    ), metrics
    assert metrics["source_mistakes"] == [], metrics


if __name__ == "__main__":
    raw = CASE_FILE.read_bytes()
    print(json.dumps({
        "suite": "task_intent_validation_v0",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": len(_load_cases()),
        **_metrics(_load_cases()),
    }, ensure_ascii=False, indent=2))
