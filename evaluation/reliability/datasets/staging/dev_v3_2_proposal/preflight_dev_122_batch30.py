"""Offline-only preflight for the 30 new Dev cases extending the run to 50."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evaluation.reliability.case_loader import load_eval_suite
from evaluation.reliability.curriculum_fixture import seed_eval_curriculum
from evaluation.reliability.fixtures.paper import seed_success_question_bank
from evaluation.reliability.runner import apply_case_setup, create_eval_session

ROOT = Path(__file__).parent
DEV = ROOT / "dev_122_revised_candidate.yaml"
DEVX = ROOT / "new_capability_20_candidate.yaml"
REPORT = ROOT / "dev_122_batch30_preflight.json"
SELECTED = (
    "EVAL-001", "EVAL-008", "EVAL-012", "EVAL-025", "EVAL-031", "EVAL-044",
    "EVAL-072", "EVAL-073", "EVAL-074", "EVAL-075", "EVAL-108", "EVAL-110", "EVAL-114",
    "EVAL-116", "EVAL-118", "EVAL-146", "EVAL-147", "EVAL-148", "EVAL-153", "EVAL-155",
    "EVAL-235", "EVAL-237", "EVAL-238", "EVAL-240",
    "EVAL-272", "EVAL-273", "EVAL-275", "EVAL-277", "EVAL-278", "EVAL-280",
)
EXECUTED_DEV = {
    "EVAL-007", "EVAL-011", "EVAL-045", "EVAL-071", "EVAL-107", "EVAL-113",
    "EVAL-119", "EVAL-144", "EVAL-151", "EVAL-234", "EVAL-236", "EVAL-239",
    "EVAL-271", "EVAL-274", "EVAL-276",
}
EXECUTED_DEVX = {"DEVX-004", "DEVX-005", "DEVX-008", "DEVX-011", "DEVX-013"}
IMPLEMENTED_GRADERS = {"state", "acceptance", "workflow_safety", "constraint_preservation", "score"}


def fingerprint(case) -> str:
    value = {key: case.raw.get(key) for key in ("turns", "setup", "expected", "graders")}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def main() -> None:
    suite = load_eval_suite(DEV)
    selected = {case.id: case for case in suite.cases if case.id in SELECTED}
    executed = [case for case in suite.cases if case.id in EXECUTED_DEV]
    executed.extend(
        case for case in load_eval_suite(DEVX).cases if case.id in EXECUTED_DEVX
    )
    executed_fingerprints = {fingerprint(case): case.id for case in executed}
    selected_fingerprints: dict[str, str] = {}
    results = []

    for case_id in SELECTED:
        case = selected.get(case_id)
        errors = []
        if case is None:
            errors.append("case_not_loaded")
        else:
            if (case.raw.get("review") or {}).get("open_issues"):
                errors.append("open_review_issue")
            value = fingerprint(case)
            if value in executed_fingerprints:
                errors.append(f"duplicates_executed:{executed_fingerprints[value]}")
            if value in selected_fingerprints:
                errors.append(f"duplicates_selected:{selected_fingerprints[value]}")
            selected_fingerprints[value] = case.id
            grader_types = {item.get("type") for item in case.graders}
            unsupported = grader_types - IMPLEMENTED_GRADERS
            if unsupported:
                errors.append(f"unsupported_graders:{sorted(unsupported)}")
            if "state" not in grader_types or "acceptance" not in grader_types:
                errors.append("incomplete_oracle")
            session = create_eval_session()
            try:
                seed_eval_curriculum(session)
                seed_success_question_bank(session)
                apply_case_setup(
                    session=session,
                    conversation_id=f"preflight-batch30-{case.id}",
                    case=case,
                )
            except Exception as exc:
                errors.append(f"fixture:{type(exc).__name__}:{exc}")
            finally:
                session.close()
        results.append({"case_id": case_id, "passed": not errors, "errors": errors})
        print(f"{case_id}: {'PASS' if not errors else 'FAIL'}")

    payload = {
        "api_calls": 0,
        "selected": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "category_counts": {
            category: sum(
                selected[case_id].category == category for case_id in SELECTED
            )
            for category in ("generation", "pending", "paper", "teaching", "multi_turn")
        },
        "results": results,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Report: {REPORT}")
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
