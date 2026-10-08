from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import FAILURE_TAGS, JudgeOutput, parse_judge_output

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
PROMPTS = {
    "teaching_design": "teaching_design_judge.md",
    "paper_quality": "paper_quality_judge.md",
    "consultation": "consultation_judge.md",
}


def build_judge_input(case: dict[str, Any], actual_output: Any) -> dict[str, Any]:
    axes = case["expected"]["behavioral_rubric"]["axes"]
    if not isinstance(axes, dict):
        raise ValueError("approved cases must use case-local axis weights")
    if abs(sum(axes.values()) - 1.0) > 1e-9:
        raise ValueError("case-local axis weights must sum to 1.0")
    return {
        "case_id": case["case_id"],
        "suite": case["suite"],
        "user_turns": case["user_turns"],
        "setup": case["setup"],
        "actual_output": actual_output,
        "behavioral_axes": axes,
        "reference_notes": case["reference_notes"],
    }


def render_judge_prompt(case: dict[str, Any], actual_output: Any) -> str:
    try:
        template = (PROMPT_DIR / PROMPTS[case["suite"]]).read_text()
    except KeyError as exc:
        raise ValueError(f"unsupported Teaching Quality suite: {case['suite']}") from exc
    judge_input = build_judge_input(case, actual_output)
    axes = list(judge_input["behavioral_axes"])
    output_requirements = {
        "case_id": case["case_id"],
        "scores": {axis: "integer 1-5" for axis in axes},
        "critical_quality_failure": "boolean",
        "failure_tags": f"array; allowed values: {sorted(FAILURE_TAGS)}",
        "rationale": {axis: "non-empty evidence-based string" for axis in axes},
        "overall_comment": "string",
    }
    return (
        f"{template.rstrip()}\n\n"
        "AXIS_INDEPENDENCE\n"
        "Score each requested axis independently from evidence specific to that axis. "
        "Do not automatically lower every axis because one central defect is severe. "
        "The same excerpt may affect multiple axes only when the rationale explains a distinct "
        "axis-specific consequence for each one. A low score on one axis does not imply a low "
        "score on another.\n\n"
        "OUTPUT_REQUIREMENTS\n"
        "Return all six top-level keys shown below. Do not omit empty arrays or comments. "
        "scores and rationale must contain exactly the listed axes.\n"
        f"{json.dumps(output_requirements, ensure_ascii=False)}\n\n"
        f"INPUT_JSON\n{json.dumps(judge_input, ensure_ascii=False)}\n"
    )


def parse_case_judge_output(case: dict[str, Any], payload: dict[str, Any]) -> JudgeOutput:
    axes = case["expected"]["behavioral_rubric"]["axes"]
    if not isinstance(axes, dict):
        raise ValueError("approved cases must use case-local axis weights")
    return parse_judge_output(payload, case_id=case["case_id"], axis_weights=axes)
