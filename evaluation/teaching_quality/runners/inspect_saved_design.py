"""Offline-only structural inspection of one frozen Teaching Quality Dev design output.

Never calls the Tutor or Judge model; never reads or runs Blind case content.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.teaching_quality.evaluators.structural import StructuralAssertionGrader
from evaluation.teaching_quality.runners.dev_contract import adapt_dev_observation
from evaluation.teaching_quality.runners.run_dev_eval import load_dev_cases
from evaluation.teaching_quality.runners.scope_review import assess_scope


def inspect(report: dict, case: dict) -> dict:
    if (report.get("kind") != "diagnostic_real_agent_dev_capture"
            or report.get("blind_executed") is not False
            or report.get("official_benchmark") is not False):
        raise ValueError("Only diagnostic Dev capture reports are supported")
    rows = [r for r in report.get("results", []) if r.get("case_id") == case["case_id"]]
    if len(rows) != 1 or rows[0].get("execution_status") != "CAPTURED":
        raise ValueError("Exactly one captured Dev case is required")
    actual = rows[0].get("actual_output") or {}
    if not isinstance(actual.get("teaching_design"), dict):
        raise ValueError("No actual teaching_design; cannot inspect")
    checks = []
    grader = StructuralAssertionGrader()
    for a in case["expected"]["structural_assertions"]:
        if a.get("match_mode") == "semantic" or a["type"] in {
            "source_grounded", "reference_used", "insufficiency_disclosed"
        }:
            checks.append({"assertion_id": a["id"], "status": "NEEDS_SEMANTIC_JUDGE"})
            continue
        assertion, projection, provenance = adapt_dev_observation(a, actual)
        if assertion.get("type") == "allowed_scope_only" and assertion.get("target") == "teaching_design.topics":
            forbidden = [term for rule in case["expected"]["structural_assertions"]
                         if rule.get("type") == "forbidden_content" for term in rule.get("forbidden", [])]
            review = assess_scope(assertion, projection.get("teaching_design", {}).get("topics", []), case["setup"], forbidden=forbidden)
            if review["status"] in {"NEEDS_SEMANTIC_REVIEW", "MISSING_OBSERVATION"}:
                checks.append({"assertion_id": a["id"], "status": review["status"],
                               "reason": "Lexical mismatch does not establish curricular overreach" if review["status"] == "NEEDS_SEMANTIC_REVIEW" else "No observed topic list",
                               "review": review, "critical": bool(a.get("critical")),
                               "derivation": provenance})
                continue
            if review["status"] == "PASS_LEXICAL":
                projection["teaching_design"]["topics"] = review["canonical"]
                provenance = provenance + ["strict lexical aliases verified without broadening allowed scope"]
        try:
            scored = grader.grade(assertion, projection, case["setup"])
            checks.append({"assertion_id": a["id"], "status": "PASS" if scored.passed else "FAIL",
                           "reason": scored.reason, "critical": scored.critical,
                           "derivation": provenance})
        except (KeyError, ValueError, TypeError) as exc:
            checks.append({"assertion_id": a["id"], "status": "UNSCORABLE", "reason": str(exc)})
    return {"kind": "offline_dev_design_structural_inspection", "case_id": case["case_id"],
            "official_benchmark": False, "model_calls": 0, "blind_executed": False,
            "agent_status": rows[0]["agent_status"], "checks": checks}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--case-id", type=str, default="TQ-TD-013")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    cases, _ = load_dev_cases()
    by_id = {c["case_id"]: c for c in cases if c["suite"] == "teaching_design"}
    if args.case_id not in by_id:
        p.error("Case must be a frozen Teaching Quality Dev teaching_design case")
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = inspect(report, by_id[args.case_id])
    output = args.output or args.input.with_name(f"offline-{args.case_id.lower()}-structural.json")
    if output.resolve() == args.input.resolve() or output.exists():
        p.error("Refusing to overwrite existing report")
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AUDIT={output}")
    for row in result["checks"]:
        print(f"{row['assertion_id']}: {row['status']} {row.get('reason','')}")
    print("MODEL_CALLS=0 BLIND_EXECUTED=False OFFICIAL_BENCHMARK=False")


if __name__ == "__main__":
    main()
