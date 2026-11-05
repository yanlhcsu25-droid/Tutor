"""Offline-only preflight for the selected Dev V3.2 batch of 15."""

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
REPORT = ROOT / "dev_122_batch15_preflight.json"
SELECTED = (
    "EVAL-007", "EVAL-011", "EVAL-045",
    "EVAL-071", "EVAL-107", "EVAL-113",
    "EVAL-119", "EVAL-144", "EVAL-151",
    "EVAL-234", "EVAL-236", "EVAL-239",
    "EVAL-271", "EVAL-274", "EVAL-276",
)
COMPLETED_DEVX = {"DEVX-004", "DEVX-005", "DEVX-008", "DEVX-011", "DEVX-013"}
IMPLEMENTED_GRADERS = {"state", "acceptance", "workflow_safety", "constraint_preservation", "score"}


def fingerprint(case) -> str:
    value = {key: case.raw.get(key) for key in ("turns", "setup", "expected", "graders")}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def main() -> None:
    suite = load_eval_suite(DEV)
    selected = {case.id: case for case in suite.cases if case.id in SELECTED}
    devx = load_eval_suite(DEVX)
    completed_fingerprints = {
        fingerprint(case): case.id for case in devx.cases if case.id in COMPLETED_DEVX
    }
    results = []
    for case_id in SELECTED:
        case = selected.get(case_id)
        errors = []
        if case is None:
            errors.append("case_not_loaded")
        else:
            if (case.raw.get("review") or {}).get("open_issues"):
                errors.append("open_review_issue")
            if fingerprint(case) in completed_fingerprints:
                errors.append(f"duplicates_{completed_fingerprints[fingerprint(case)]}")
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
                    conversation_id=f"preflight-{case.id}",
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
        "results": results,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Report: {REPORT}")
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
