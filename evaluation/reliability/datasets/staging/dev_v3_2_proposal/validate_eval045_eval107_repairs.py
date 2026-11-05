"""Offline scripted regression for EVAL-045 and EVAL-107 repairs."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.reliability.case_loader import load_eval_suite
from evaluation.reliability.runner import run_case, write_report

ROOT = Path(__file__).parent
CASES = ROOT / "dev_122_revised_candidate.yaml"
REPORT = ROOT / "eval045_eval107_repair_regression_offline.json"


def tool_call(name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"repair-{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
    }


def main() -> None:
    suite = load_eval_suite(CASES)
    cases = {case.id: case for case in suite.cases if case.id in {"EVAL-045", "EVAL-107"}}
    cases["EVAL-045"].backend = {"behavior": "scripted", "responses": [
        tool_call("prepare_generation_plan", {
            "paper_type": "chapter_test",
            "scope_names": ["第一章"],
            "question_count": 5,
            "total_score": 50,
            "question_type_requirements": [
                {"question_type": "选择题", "count": 2},
                {"question_type": "计算题", "count": 3},
            ],
        }),
        {"role": "assistant", "content": "已创建第一章5题50分草案，请确认。"},
    ]}
    cases["EVAL-107"].backend = {"behavior": "scripted", "responses": [
        tool_call("discard_pending_plan", {}),
        {"role": "assistant", "content": "已取消。"},
    ]}

    results = [run_case(cases[case_id]) for case_id in ("EVAL-045", "EVAL-107")]
    write_report(results, REPORT, dataset_path=CASES, case_delay_seconds=0)
    for result in results:
        print(f"{result['case_id']}: {'PASS' if result['passed'] else 'FAIL'}")
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
