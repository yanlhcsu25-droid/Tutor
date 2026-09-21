"""Frozen 100-case validation set for top-level Teacher Agent routing."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import yaml

from calculus_agent.agent.task_router import RoutingState, decide_task


CASE_FILE = Path(__file__).parent / "cases" / "task_intent_validation_v0.yaml"
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
    return RoutingState(
        pending_generation=bool(raw.get("has_pending")),
        current_paper=bool(raw.get("current_paper")),
    )


def _metrics(cases: list[dict]) -> dict:
    confusion: dict[str, Counter] = defaultdict(Counter)
    mistakes: list[dict] = []
    for case in cases:
        actual = decide_task(case["text"], state=_state(case.get("state"))).route.task_type.value
        expected = case["expected"]
        confusion[expected][actual] += 1
        if actual != expected:
            mistakes.append({
                "id": case["id"],
                "text": case["text"],
                "expected": expected,
                "actual": actual,
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
    }


def test_intent_validation_dataset_is_frozen_balanced_and_unique() -> None:
    cases = _load_cases()

    assert len(cases) == 100
    assert len({case["id"] for case in cases}) == 100
    assert len({case["text"] for case in cases}) == 100
    assert Counter(case["expected"] for case in cases) == Counter({label: 25 for label in LABELS})


def test_task_router_meets_validation_quality_gate() -> None:
    metrics = _metrics(_load_cases())

    assert metrics["accuracy"] >= 0.90, metrics
    assert metrics["macro_f1"] >= 0.90, metrics
    assert all(
        item["recall"] >= 0.80
        for item in metrics["per_label"].values()
    ), metrics


if __name__ == "__main__":
    raw = CASE_FILE.read_bytes()
    print(json.dumps({
        "suite": "task_intent_validation_v0",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": len(_load_cases()),
        **_metrics(_load_cases()),
    }, ensure_ascii=False, indent=2))
