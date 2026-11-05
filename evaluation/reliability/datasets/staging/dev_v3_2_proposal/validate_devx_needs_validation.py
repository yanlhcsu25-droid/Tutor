"""Run the six Dev V3.2 validation candidates without external model APIs."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.reliability.case_loader import load_eval_suite
from evaluation.reliability.runner import run_case, write_report

ROOT = Path(__file__).parent
CASES = ROOT / "new_capability_20_candidate.yaml"
REPORT = ROOT / "devx_needs_validation_offline_run.json"
CASE_IDS = ("DEVX-004", "DEVX-005", "DEVX-008", "DEVX-011", "DEVX-013", "DEVX-015")


def tool_call(name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"offline-{name}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }],
    }


def final(content: str) -> dict:
    return {"role": "assistant", "content": content}


SCRIPTS = {
    "DEVX-004": [
        tool_call("read_paper", {"positions": [3]}),
        final("全卷第3题为选择题第3题。"),
        tool_call("read_paper", {"positions": [3]}),
        tool_call("preview_paper_changes", {"operations": [{
            "type": "replace_question",
            "target": {"section_type": "选择题", "section_order": 3},
            "difficulty_direction": "easier",
        }]}),
        final("已创建更容易的同题型替换预览，尚未应用，请确认。"),
    ],
    "DEVX-005": [
        tool_call("read_paper", {"positions": [5]}),
        final("全卷第5题为填空题第1题。"),
        tool_call("read_paper", {"positions": [5]}),
        tool_call("preview_paper_changes", {"operations": [{
            "type": "remove_question",
            "target": {"section_type": "填空题", "section_order": 1},
        }]}),
        final("已创建删除预览，尚未应用，请确认。"),
    ],
    "DEVX-008": [
        tool_call("prepare_generation_plan", {
            "paper_type": "chapter_test",
            "scope_names": ["第一章 函数与极限"],
            "question_count": 6,
            "total_score": 60,
            "question_type_requirements": [
                {"question_type": "选择题", "count": 2},
                {"question_type": "填空题", "count": 2},
                {"question_type": "计算题", "count": 2},
            ],
        }),
        final("已按6题60分及三类各2题创建方案，尚未生成。"),
    ],
    "DEVX-011": [
        tool_call("prepare_generation_plan", {"question_count": 7, "total_score": 100}),
        final("已将当前方案改为7题并保持100分和章节不变，等待确认。"),
    ],
    "DEVX-013": [
        tool_call("discard_pending_plan", {}),
        final("已取消组卷待确认方案，原卷未修改。"),
    ],
    "DEVX-015": [
        tool_call("discard_pending_plan", {}),
        final("仅取消了组卷待确认方案，教学设计保持原样。"),
    ],
}


def main() -> None:
    suite = load_eval_suite(CASES)
    selected = {case.id: case for case in suite.cases if case.id in CASE_IDS}
    if set(selected) != set(CASE_IDS):
        raise RuntimeError(f"missing cases: {sorted(set(CASE_IDS) - set(selected))}")

    results = []
    for case_id in CASE_IDS:
        case = selected[case_id]
        case.backend = {"behavior": "scripted", "responses": SCRIPTS[case_id]}
        result = run_case(case)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        actual_status = (result.get("actual") or {}).get("status")
        print(f"{case_id}: {status} ({actual_status})")

    write_report(results, REPORT, dataset_path=CASES, case_delay_seconds=0)
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
