from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tests.evals.teaching_quality.grader.behavioral import build_judge_input, render_judge_prompt
from tests.evals.teaching_quality.grader.models import FAILURE_TAGS, JudgeOutput, parse_judge_output
from tests.evals.teaching_quality.runner.validate_candidates import (
    validate_case_metadata,
)

ROOT = Path(__file__).resolve().parents[1]


def valid_payload():
    return {
        "case_id": "TQ-TD-001",
        "scores": {"objective_alignment": 4, "feasibility": 5},
        "critical_quality_failure": False,
        "failure_tags": [],
        "rationale": {"objective_alignment": "aligned", "feasibility": "fits"},
        "overall_comment": "usable",
    }


def test_judge_json_schema_is_valid_json_and_forbids_extra_fields():
    schema = json.loads((ROOT / "schema/judge_output.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(JudgeOutput.model_fields)
    assert set(schema["properties"]["failure_tags"]["items"]["enum"]) == FAILURE_TAGS
    payload = valid_payload() | {"unexpected": True}
    with pytest.raises(ValidationError):
        parse_judge_output(
            payload, case_id="TQ-TD-001",
            axis_weights={"objective_alignment": 0.5, "feasibility": 0.5},
        )


def test_suite_specific_prompt_contains_complete_judge_input():
    case, _ = approved_case()
    judge_input = build_judge_input(case, {"teaching_design": {"title": "test"}})
    assert set(judge_input) == {
        "case_id", "suite", "user_turns", "setup", "actual_output",
        "behavioral_axes", "reference_notes",
    }
    prompt = render_judge_prompt(case, judge_input["actual_output"])
    assert "Teaching Design Quality Judge" in prompt
    assert case["case_id"] in prompt


def test_judge_axes_must_match_case_exactly():
    payload = valid_payload()
    payload["scores"]["clarity"] = 4
    with pytest.raises(ValueError, match="axes mismatch"):
        parse_judge_output(
            payload, case_id="TQ-TD-001",
            axis_weights={"objective_alignment": 0.5, "feasibility": 0.5},
        )


def test_judge_missing_axis_fails():
    payload = valid_payload()
    payload["scores"].pop("feasibility")
    with pytest.raises(ValueError, match="axes mismatch"):
        parse_judge_output(
            payload, case_id="TQ-TD-001",
            axis_weights={"objective_alignment": 0.5, "feasibility": 0.5},
        )


def test_judge_score_must_be_in_range():
    payload = valid_payload()
    payload["scores"]["feasibility"] = 6
    with pytest.raises(ValidationError):
        parse_judge_output(
            payload, case_id="TQ-TD-001",
            axis_weights={"objective_alignment": 0.5, "feasibility": 0.5},
        )


def test_axis_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        parse_judge_output(
            valid_payload(), case_id="TQ-TD-001",
            axis_weights={"objective_alignment": 0.4, "feasibility": 0.5},
        )


def approved_case():
    payload = yaml.safe_load((ROOT / "cases/teaching_design_v1.yaml").read_text())
    return deepcopy(payload["cases"][0]), payload["defaults"]["judge_axes"]


def assertion_types():
    schema = json.loads((ROOT / "schema/teaching_quality_case.schema.json").read_text())
    return set(schema["$defs"]["assertion"]["properties"]["type"]["enum"])


def test_approved_case_without_family_id_fails_metadata_validation():
    case, axes = approved_case()
    case.pop("family_id")
    with pytest.raises(ValueError, match="fields"):
        validate_case_metadata(case, axes, assertion_types())


def test_approved_case_still_pending_fails_metadata_validation():
    case, axes = approved_case()
    case["human_review"]["status"] = "pending"
    with pytest.raises(ValueError, match="cannot remain pending"):
        validate_case_metadata(case, axes, assertion_types())
