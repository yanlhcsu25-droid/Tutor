"""Validate approved Teaching Quality metadata without running a model."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.evals.teaching_quality.grader.structural import SUPPORTED_ASSERTION_TYPES

EXPECTED_COUNTS = {
    "teaching_design_v1.yaml": 12,
    "paper_quality_v1.yaml": 13,
    "consultation_v1.yaml": 11,
}
REQUIRED_CASE_KEYS = {
    "case_id", "family_id", "suite", "category", "priority", "difficulty", "title",
    "setup", "user_turns", "expected", "reference_notes", "human_review",
}
FAMILY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def validate_case_metadata(
    case: dict[str, Any], default_axes: dict[str, float], assertion_types: set[str]
) -> None:
    case_id = case.get("case_id", "<missing>")
    if set(case) != REQUIRED_CASE_KEYS:
        raise ValueError(f"{case_id}: case fields do not match schema contract")
    family_id = case.get("family_id")
    if not isinstance(family_id, str) or not FAMILY_PATTERN.fullmatch(family_id):
        raise ValueError(f"{case_id}: family_id must be non-empty snake_case")
    review = case["human_review"]
    if review.get("status") != "approved":
        raise ValueError(f"{case_id}: approved Quality candidate cannot remain pending")
    if not str(review.get("reviewer_notes", "")).strip():
        raise ValueError(f"{case_id}: approved review requires reviewer_notes")

    case_axes = case["expected"]["behavioral_rubric"]["axes"]
    if not isinstance(case_axes, dict):
        raise TypeError(f"{case_id}: approved case requires case-local axis weights")
    if not set(case_axes) <= set(default_axes):
        raise ValueError(f"{case_id}: unknown behavioral axis")
    if abs(sum(case_axes.values()) - 1.0) > 1e-9:
        raise ValueError(f"{case_id}: case-local axis weights must sum to 1.0")

    for assertion in case["expected"]["structural_assertions"]:
        assertion_type = assertion["type"]
        if assertion_type not in assertion_types:
            raise ValueError(f"{case_id}: unknown assertion type {assertion_type}")
        if assertion_type not in SUPPORTED_ASSERTION_TYPES:
            raise ValueError(f"{case_id}: assertion type has no runtime grader {assertion_type}")
        expected = assertion.get("expected")
        if assertion_type == "required_content" and assertion.get("match_mode") != "semantic":
            raise ValueError(f"{case_id}: required_content must use semantic matching")
        if assertion_type == "forbidden_content" and assertion.get("match_mode") != "semantic":
            raise ValueError(f"{case_id}: forbidden_content must use semantic matching")
        if (
            case["suite"] == "teaching_design"
            and assertion_type == "allowed_scope_only"
            and isinstance(expected, list)
            and expected
            and all(str(item).endswith("章") for item in expected)
            and assertion.get("target") != "teaching_design.chapters"
        ):
            raise ValueError(f"{case_id}: chapter scope must target derived chapters")
        if (
            assertion_type == "count_min_per_topic"
            and assertion.get("target")
            not in {"teaching_design.examples", "teaching_design.misconceptions"}
        ):
            raise ValueError(f"{case_id}: count_min_per_topic must target real objects")


def main() -> None:
    schema = json.loads((ROOT / "schema/teaching_quality_case.schema.json").read_text())
    json.loads((ROOT / "schema/judge_output.schema.json").read_text())
    assertion_types = set(schema["$defs"]["assertion"]["properties"]["type"]["enum"])
    seen: set[str] = set()

    for path in sorted((ROOT / "cases").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text())
        cases = payload["cases"]
        if len(cases) != EXPECTED_COUNTS[path.name]:
            raise ValueError(f"{path}: unexpected case count")
        if payload["suite"]["split"] != "candidate" or payload["suite"]["status"] != "approved":
            raise ValueError(f"{path}: suite must be approved candidate data")
        axes = payload["defaults"]["judge_axes"]
        if abs(sum(axes.values()) - 1.0) > 1e-9:
            raise ValueError(f"{path}: default weights must sum to 1.0")

        for case in cases:
            validate_case_metadata(case, axes, assertion_types)
            case_id = case["case_id"]
            if case_id in seen:
                raise ValueError(f"duplicate case_id: {case_id}")
            seen.add(case_id)
        print(f"{path.name}: {len(cases)} approved candidates")

    if len(seen) != 36:
        raise ValueError(f"expected 36 unique Quality cases, found {len(seen)}")

    moved_from: set[str] = set()
    structural_counts = {"paper_v1.yaml": 7, "teaching_design_v1.yaml": 5}
    for path in sorted((ROOT.parent / "structural_regression/cases").glob("*.yaml")):
        structural = yaml.safe_load(path.read_text())
        if len(structural["cases"]) != structural_counts[path.name]:
            raise ValueError(f"{path}: unexpected structural case count")
        moved_from.update(case["source_case_id"] for case in structural["cases"])
        print(f"{path.name}: {len(structural['cases'])} deterministic regression candidates")

    clarification_path = ROOT.parent / "clarification_regression/cases/teaching_design_v1.yaml"
    clarification = yaml.safe_load(clarification_path.read_text())
    if len(clarification["cases"]) != 1:
        raise ValueError(f"{clarification_path}: unexpected clarification case count")
    moved_from.update(case["source_case_id"] for case in clarification["cases"])
    if seen & moved_from:
        raise ValueError("moved cases still exist in Teaching Quality")
    print("teaching_design_v1.yaml: 1 clarification regression candidate")
    print("OK: 36 approved quality + 12 structural + 1 clarification; no model invoked")


if __name__ == "__main__":
    main()
