"""Dry-run the patched opt-in guard against saved TeachingQuality Dev failure traces.

Zero model calls. Only reads a caller-specified report, never reads a Blind file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from calculus_agent.runtime.request_guards import _apply_explicit_opt_in_guards
from calculus_agent.teaching_design.schemas import TeachingDesignContent

CASES = ("TQ-TD-006", "TQ-TD-009", "TQ-TD-013")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if data.get("kind") != "diagnostic_real_agent_dev_capture" or data.get("blind_executed") is not False:
        parser.error("Refusing non-Dev diagnostic report")
    if data.get("official_benchmark") is not False or data.get("dev_case_count") != 24:
        parser.error("Refusing report without 24-case Dev diagnostic marker")
    selected = {r["case_id"]: r for r in data.get("results", []) if r.get("case_id") in CASES}
    if set(selected) != set(CASES):
        parser.error("Expected the 3 known Dev failing cases; input may not be the expected report")
    bad = 0
    for case_id in CASES:
        case = selected[case_id]
        if case.get("agent_status") != "failed":
            parser.error(f"Expected a failed Agent case: {case_id}")
        turn = case["turns"][-1]
        traces = (turn.get("trace") or {}).get("tool_calls") or []
        matching = [call for call in traces if call.get("tool_name") == "create_teaching_design" and
                    (call.get("result") or {}).get("code") == "invalid_tool_arguments"]
        if not matching:
            parser.error(f"Missing prior failing create_teaching_design call: {case_id}")
        argument = matching[-1]["arguments"]
        old = argument["content"]["assessment_plan"]["question_type_requirements"]
        guarded = _apply_explicit_opt_in_guards(
            tool_name="create_teaching_design", arguments=argument, message=turn["user_message"],
        )
        new = guarded["content"]["assessment_plan"]["question_type_requirements"]
        if not any("score_each" in q for q in old) or any("score_each" in q for q in new):
            bad += 1
            print(f"{case_id}: FAILED GUARD (score_each not properly removed)")
            continue
        try:
            TeachingDesignContent.model_validate(guarded["content"])
            print(f"{case_id}: GUARD_OK, payload schema-valid; saved Agent outcome unchanged")
        except Exception as e:
            bad += 1
            print(f"{case_id}: GUARD_OK, OTHER_VALIDATION_ERROR: {type(e).__name__}: {e}")
    print(f"DRY_RUN={len(CASES)} FAILED={bad} MODEL_CALLS=0 BLIND_EXECUTED=False")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
