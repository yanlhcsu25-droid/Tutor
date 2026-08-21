from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evals.case_loader import load_eval_suite
from tests.evals.runner import run_case


CASE_FILE = Path(
    "tests/evals/workflow_routing.yaml"
)


REPORT_FILE = Path(
    "tests/evals/reports/workflow_routing.json"
)


def _extract_workflow(result: dict) -> str | None:
    """
    Extract workflow decision from runtime trace.

    Different runtime implementations may store this in
    different places, so keep this tolerant.
    """

    for turn in result.get("turns", []):

        observations = (
            turn.get("observability")
            or {}
        )

        workflow = observations.get(
            "workflow"
        )

        if workflow:
            return workflow

        metadata = (
            turn.get("metadata")
            or {}
        )

        workflow = metadata.get(
            "workflow"
        )

        if workflow:
            return workflow

    return None


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM") != "1",
    reason="Workflow routing eval requires RUN_LIVE_LLM=1",
)
@pytest.mark.parametrize(
    "case_index",
    [0, 1, 2],
)
def test_workflow_routing(case_index: int):

    suite = load_eval_suite(
        CASE_FILE
    )

    case = suite.cases[case_index]

    result = run_case(case)

    expected = (
        case.expected["workflow"]
    )

    actual = _extract_workflow(
        result
    )

    report = {
        "case_id": case.id,
        "expected": expected,
        "actual": actual,
        "success": (
            expected == actual
        ),
        "runtime_error": result.get(
            "error"
        ),
    }

    REPORT_FILE.parent.mkdir(
        exist_ok=True
    )

    REPORT_FILE.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )

    assert actual == expected