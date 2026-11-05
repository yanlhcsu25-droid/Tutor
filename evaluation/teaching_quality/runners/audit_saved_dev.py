"""Offline audit of a saved Dev capture. Never invokes an Agent or Judge model."""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation.teaching_quality.evaluators.models import JudgeOutput, StructuralAssertionResult
from evaluation.teaching_quality.evaluators.scoring import score_case
from evaluation.teaching_quality.evaluators.structural import StructuralAssertionGrader
from evaluation.teaching_quality.runners.dev_contract import adapt_dev_observation
from evaluation.teaching_quality.runners.run_dev_eval import load_dev_cases

SEMANTIC = {"source_grounded", "reference_used", "insufficiency_disclosed"}


def audit_report(report: dict, cases: list[dict]) -> dict:
    if report.get("blind_executed") is not False or report.get("official_benchmark") is not False:
        raise ValueError("Only diagnostic Dev reports are allowed")
    if report.get("kind") != "diagnostic_real_agent_dev_capture":
        raise ValueError("Unknown report kind")
    by_id = {c["case_id"]: c for c in cases}
    if len(by_id) != len(cases):
        raise ValueError("Duplicate cases")
    output = []
    seen = set()
    for result in report["results"]:
        cid = result["case_id"]
        if cid not in by_id or cid in seen:
            raise ValueError(f"Case not in frozen Dev or duplicated: {cid}")
        seen.add(cid)
        case = by_id[cid]
        status = result.get("execution_status", "")
        agent_status = result.get("agent_status")
        previous = result.get("grading") or {}
        record = {"case_id": cid, "suite": case["suite"], "execution_status": status,
                  "agent_status": agent_status, "previous_grading": previous.get("status"),
                  "offline_status": None, "missing_assertions": [],
                  "derived_observations": {}, "score": None, "issues": []}
        if status == "SKIPPED_UNSEEDED_FIXTURE":
            record["offline_status"] = "BLOCKED_UNSEEDED_FIXTURE"
            record["issues"].append("controlled question-bank fixture missing")
        elif status != "CAPTURED":
            record["offline_status"] = "EXECUTION_ERROR"
        elif agent_status == "failed":
            record["offline_status"] = "AGENT_EXECUTION_FAILED"
            record["issues"].append("Agent tool arguments or runtime failure; not a Judge error")
        elif agent_status in {"needs_clarification"}:
            record["offline_status"] = "NEEDS_CLARIFICATION_REVIEW"
            record["issues"].append("Verify whether clarification was justified")
        elif case["suite"] == "teaching_design" and "teaching_design" not in result.get("actual_output", {}):
            record["offline_status"] = "REQUIRED_ARTIFACT_MISSING"
            record["issues"].append("No structured teaching_design: do not invent one from prose")
        else:
            existing = {item["assertion_id"]: item for item in previous.get("structural", [])}
            actual = result.get("actual_output", {})
            grader = StructuralAssertionGrader()
            observations = []
            for assertion in case["expected"]["structural_assertions"]:
                aid = assertion["id"]
                sem = assertion["type"] in SEMANTIC or assertion.get("match_mode") == "semantic"
                if sem:
                    if aid in existing:
                        observations.append(StructuralAssertionResult.model_validate(existing[aid]))
                    else:
                        record["missing_assertions"].append({"assertion_id": aid, "why": "semantic judge result not cached"})
                    continue
                adapted, projected, notes = adapt_dev_observation(assertion, actual)
                if notes:
                    record["derived_observations"][aid] = notes
                try:
                    observations.append(grader.grade(adapted, projected, case["setup"]))
                except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
                    record["missing_assertions"].append({"assertion_id": aid, "why": f"{type(exc).__name__}: {exc}"})
            judge_data = previous.get("behavioral")
            allowed_for_score = agent_status in {"completed", "waiting_confirmation"}
            if record["missing_assertions"] or not judge_data or not allowed_for_score:
                record["offline_status"] = "INCOMPLETE_EVIDENCE"
                if not judge_data:
                    record["issues"].append("Behavioral Judge was not called; no offline scoring possible")
            else:
                score = score_case(
                    observations,
                    JudgeOutput.model_validate(judge_data),
                    case["expected"]["behavioral_rubric"]["axes"],
                )
                record["score"] = score.model_dump(mode="json")
                record["offline_status"] = "DIAGNOSTIC_SCORED"
        output.append(record)
    if len(output) != report.get("dev_case_count"):
        raise ValueError("Report missing cases; do not present as complete")
    counts = Counter(row["offline_status"] for row in output)
    scored = [row for row in output if row["offline_status"] == "DIAGNOSTIC_SCORED"]
    return {
        "kind": "offline_dev_contract_audit_v1",
        "official_benchmark": False,
        "blind_executed": False,
        "model_calls": 0,
        "dev_case_count": len(output),
        "summary": {
            "status_counts": dict(counts),
            "scored_count": len(scored),
            "pass": sum(r["score"]["status"] == "PASS" for r in scored),
            "partial": sum(r["score"]["status"] == "PARTIAL" for r in scored),
            "fail": sum(r["score"]["status"] == "FAIL" for r in scored),
            "official_accuracy": None,
        },
        "results": output,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    cases, _ = load_dev_cases()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = audit_report(report, cases)
    target = args.output or args.input.with_name("offline-contract-audit.json")
    if target.resolve() == args.input.resolve():
        p.error("Refusing to overwrite raw Agent report")
    if target.exists():
        p.error("Output exists; refusing to overwrite")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"AUDIT={target}")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print("NOTE: Offline diagnostic. No model calls and no official accuracy.")


if __name__ == "__main__":
    main()
