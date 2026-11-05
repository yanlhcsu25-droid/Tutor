"""Validate the 29 deterministic Dev V3.2 review repairs without model APIs."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from evaluation.reliability.case_loader import load_eval_suite
from evaluation.reliability.curriculum_fixture import seed_eval_curriculum
from evaluation.reliability.fixtures.paper import seed_success_question_bank
from evaluation.reliability.runner import apply_case_setup, create_eval_session, run_graders

ROOT = Path(__file__).parent
CASES = ROOT / "dev_122_revised_candidate.yaml"
RESOLVED = ROOT / "resolved_deterministic_review_items.json"
REPORT = ROOT / "deterministic_review_repairs_validation.json"


def call(name: str, *, content: str | None = None) -> dict:
    result = {"ok": True}
    if content is not None:
        result["questions"] = [{"position": 1, "content": content}]
    return {"tool_name": name, "output_json": result}


def matching_message(case_id: str, expected: dict) -> str:
    facts = expected.get("response_facts") or {}
    parts = []
    if "question_count" in facts:
        parts.append(f"共{facts['question_count']}题")
    if "total_score" in facts:
        parts.append(f"总分{facts['total_score']}分")
    for question_type, count in (facts.get("question_type_counts") or {}).items():
        parts.append(f"{question_type}{count}题")
    for level, count in (facts.get("difficulty_counts") or {}).items():
        parts.append(f"难度{level}：{count}题")
    return "，".join(parts) or f"Observed content for {case_id}"


def synthetic_actual(case) -> dict:
    expected = deepcopy(case.expected)
    acceptance = next(
        (item for item in case.graders if item.get("type") == "acceptance"),
        {},
    )
    turn_contracts = acceptance.get("turns") or []
    turns = []
    all_calls = []
    for index, raw_turn in enumerate(case.turns, 1):
        contract = next(
            (item for item in turn_contracts if item.get("turn") == index),
            {},
        )
        names = contract.get("required_tools") or (
            acceptance.get("required_tools") if index == len(case.turns) else []
        ) or []
        content = f"Observed content for {case.id}"
        calls = [
            call(name, content=content if name == "read_paper" else None)
            for name in names
        ]
        all_calls.extend(calls)
        status = (contract.get("statuses") or [
            expected.get("status") if index == len(case.turns) else "completed"
        ])[0]
        message = matching_message(case.id, expected)
        if (case.business_rules or {}).get("require_read_content_grounding"):
            message = content
        result = {
            "status": status,
            "message": message,
            "clarification_questions": (
                ["请明确题型内题号或全卷题号。"]
                if status == "needs_clarification" else []
            ),
        }
        turns.append({
            "turn": index,
            "user": raw_turn.get("user"),
            "result": result,
            "state": {"trace": {"tool_calls": calls}},
            "business_state_before": {},
            "business_state_after": {},
        })

    actual = {
        **expected,
        "message": matching_message(case.id, expected),
        "result": {
            "status": expected.get("status"),
            "message": matching_message(case.id, expected),
            "clarification_questions": (
                ["请明确题型内题号或全卷题号。"]
                if expected.get("status") == "needs_clarification" else []
            ),
        },
        "trace": {"run_id": f"offline-{case.id}", "tool_calls": all_calls},
        "evaluated_turns": turns,
        "before": {"paper_db": expected.get("paper_db")},
        "backend_calls": 0,
    }
    return actual


def main() -> None:
    resolved_ids = {
        item["case_id"] for item in json.loads(RESOLVED.read_text(encoding="utf-8"))
    }
    suite = load_eval_suite(CASES)
    selected = [case for case in suite.cases if case.id in resolved_ids]
    if len(selected) != 29:
        raise RuntimeError(f"expected 29 repaired cases, got {len(selected)}")

    results = []
    for case in selected:
        session = create_eval_session()
        fixture_error = None
        try:
            seed_eval_curriculum(session)
            seed_success_question_bank(session)
            apply_case_setup(
                session=session,
                conversation_id=f"offline-repair-{case.id}",
                case=case,
            )
        except Exception as exc:  # reported per Case below
            fixture_error = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()

        graders = run_graders(case, synthetic_actual(case)) if not fixture_error else []
        failures = [
            result for result in graders
            if not result.get("passed") and not result.get("skipped")
        ]
        passed = fixture_error is None and not failures
        results.append({
            "case_id": case.id,
            "passed": passed,
            "fixture_error": fixture_error,
            "grader_failures": failures,
        })
        print(f"{case.id}: {'PASS' if passed else 'FAIL'}")

    payload = {
        "api_calls": 0,
        "cases": len(results),
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
