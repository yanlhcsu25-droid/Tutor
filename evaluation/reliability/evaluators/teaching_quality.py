"""Teaching-quality Judge adapter for selected reliability consultation cases."""

from __future__ import annotations

import os
from typing import Any

from calculus_agent.config import get_settings
from evaluation.teaching_quality.evaluators.behavioral import (
    judge_prompt_artifacts,
    parse_case_judge_output,
)
from evaluation.teaching_quality.evaluators.scoring import score_case
from evaluation.teaching_quality.judge.provider import build_siliconflow_judge_adapter


class TeachingQualityInfrastructureError(RuntimeError):
    """A Judge provider/schema failure, never an Agent-quality failure."""


def _judge_case(case, config: dict[str, Any]) -> dict[str, Any]:
    axes = config.get("axes")
    notes = config.get("reference_notes")
    if not isinstance(axes, dict) or not axes:
        raise ValueError("teaching_quality grader requires case-local axes")
    if not isinstance(notes, list) or not notes:
        raise ValueError("teaching_quality grader requires reference_notes")
    return {
        "case_id": config["judge_case_id"],
        "suite": "consultation",
        "user_turns": [
            {"role": "user", "content": turn["user"]}
            for turn in case.turns
        ],
        "setup": case.setup,
        "expected": {"behavioral_rubric": {"axes": axes}},
        "reference_notes": notes,
    }


def grade_teaching_quality(
    case,
    actual: dict[str, Any],
    config: dict[str, Any],
    *,
    adapter=None,
) -> dict[str, Any]:
    """Run the existing consultation Judge; explicit adapters are test-only."""
    judge_case = _judge_case(case, config)
    judge_output = {"response": str(actual.get("message") or "")}
    artifacts = judge_prompt_artifacts(judge_case, judge_output)

    if adapter is None:
        if os.getenv("RUN_LIVE_JUDGE") != "1":
            raise TeachingQualityInfrastructureError(
                "Teaching Quality Judge requires RUN_LIVE_JUDGE=1"
            )
        try:
            adapter = build_siliconflow_judge_adapter(
                get_settings(), max_tokens=2048
            )
        except Exception as exc:
            raise TeachingQualityInfrastructureError(
                f"Judge initialization failed: {type(exc).__name__}: {exc}"
            ) from exc

    try:
        payload = dict(adapter.complete(artifacts["rendered_prompt"]))
        judge = parse_case_judge_output(judge_case, payload)
        score = score_case([], judge, config["axes"])
    except Exception as exc:
        raise TeachingQualityInfrastructureError(
            f"Judge call/schema failed: {type(exc).__name__}: {exc}"
        ) from exc

    return {
        "grader": "teaching_quality",
        "passed": score.status == "PASS",
        "errors": [] if score.status == "PASS" else list(score.reasons),
        "details": {
            "score": score.model_dump(mode="json"),
            "judge_output": judge.model_dump(mode="json"),
            "prompt_artifacts": artifacts,
            "provider": adapter.provenance(),
        },
    }
