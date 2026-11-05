"""Regrade the frozen Batch 30 report after the draft-language grader fix."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluation.reliability.case_loader import load_eval_suite
from evaluation.reliability.evaluators.outcome import grade_outcome_integrity

ROOT = Path(__file__).parent
REPORTS = ROOT.parents[4] / "evaluation/artifacts/reports/reliability/runs"
SOURCE = REPORTS / "dev-v3.2-dev122-batch30-first-api.json"
TARGET = REPORTS / "dev-v3.2-dev122-batch30-first-api.regraded.json"
CASES = ROOT / "dev_122_revised_candidate.yaml"


def main() -> None:
    original = json.loads(SOURCE.read_text())
    report = copy.deepcopy(original)
    cases = {case.id: case for case in load_eval_suite(CASES).cases}
    changed = []

    for result in report["results"]:
        replacement = grade_outcome_integrity(
            cases[result["case_id"]], result["actual"],
        )
        for index, grader in enumerate(result["graders"]):
            if grader.get("grader") == "outcome_integrity":
                if grader != replacement:
                    changed.append(result["case_id"])
                result["graders"][index] = replacement
                break
        passed = bool(result["graders"]) and all(
            grader.get("passed") is True
            for grader in result["graders"]
            if not grader.get("skipped")
        )
        result["passed"] = passed
        result["task_success"] = passed
        result["false_success"] = replacement["false_success"]
        if passed:
            result["failure_type"] = None
        elif result["failure_type"] == "grounding" and not replacement["false_success"]:
            result["failure_type"] = "model_reasoning"

    passed_count = sum(item["passed"] for item in report["results"])
    report["summary"] = {
        "cases": len(report["results"]),
        "passed": passed_count,
        "failed": len(report["results"]) - passed_count,
        "pass_rate": passed_count / len(report["results"]),
    }
    report["regrade"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": str(SOURCE),
        "api_calls": 0,
        "changed_cases": changed,
        "reason": "outcome grader now distinguishes a generated draft awaiting confirmation from a persisted final Paper",
    }
    TARGET.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"changed_cases={changed}")
    print(f"Report: {TARGET}")


if __name__ == "__main__":
    main()
