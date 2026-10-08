from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import JudgeOutput, parse_judge_output

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
    return f"{template.rstrip()}\n\nINPUT_JSON\n{json.dumps(build_judge_input(case, actual_output), ensure_ascii=False)}\n"


def parse_case_judge_output(case: dict[str, Any], payload: dict[str, Any]) -> JudgeOutput:
    axes = case["expected"]["behavioral_rubric"]["axes"]
    if not isinstance(axes, dict):
        raise ValueError("approved cases must use case-local axis weights")
    return parse_judge_output(payload, case_id=case["case_id"], axis_weights=axes)
