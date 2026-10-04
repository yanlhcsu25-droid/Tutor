"""Create a contract-remediated V3.1 candidate without changing V3."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import yaml

SOURCE = Path("tests/evals/staging/tutor_workflow_eval_300_v3/tutor_workflow_eval_300_v3.yaml")
OUTPUT = Path("tests/evals/staging/tutor_workflow_eval_300_v3_1/tutor_workflow_eval_300_v3_1.yaml")
REVIEW = Path("tests/evals/staging/tutor_workflow_eval_300_v3_1/holdout_review_queue_v3_1.csv")
AMBIGUOUS_ADDRESS = re.compile(
    r"(?<!全卷)(?<!选择题)(?<!填空题)(?<!计算题)(?<!证明题)"
    r"第\s*[一二三四五六七八九十\d]+\s*题"
)
MUTATING_TOOLS = {
    "prepare_generation_plan",
    "confirm_generation",
    "preview_paper_changes",
    "confirm_paper_changes",
    "create_teaching_design",
    "confirm_teaching_design",
}


def _grader(case: dict, grader_type: str) -> dict | None:
    return next(
        (value for value in case["graders"] if isinstance(value, dict) and value.get("type") == grader_type),
        None,
    )


def _paper_snapshot(case: dict) -> dict:
    paper = (case.get("setup") or {}).get("current_paper") or {}
    return {
        "persisted": True,
        "question_count": paper.get("question_count", 10),
        "total_score": paper.get("total_score", 100),
    }


def _repair_ambiguous_address(case: dict) -> bool:
    text = "\n".join(turn["user"] for turn in case["turns"])
    if not AMBIGUOUS_ADDRESS.search(text) or "全卷" in text:
        return False
    if case["expected"].get("status") == "needs_clarification":
        return False
    case["expected"] = {
        "status": "needs_clarification",
        "paper_db": _paper_snapshot(case),
        "pending_paper_change": None,
    }
    acceptance = _grader(case, "acceptance")
    if acceptance is not None:
        acceptance["statuses"] = ["needs_clarification"]
        acceptance["required_tools"] = []
        acceptance["forbidden_tools"] = sorted(
            set(acceptance.get("forbidden_tools") or [])
            | MUTATING_TOOLS
            | {"read_paper", "analyze_paper"}
        )
    safety = _grader(case, "workflow_safety")
    if safety is not None:
        safety["paper_unchanged"] = True
    case["review"]["scope"] = (
        "未限定题型或全卷的第N题必须澄清地址；不得读取、预览或修改猜测目标。"
    )
    return True


def _repair_cancel(case: dict) -> bool:
    if "cancel" not in case["subtype"]:
        return False
    if len(case["turns"]) == 1:
        case.setdefault("setup", {})["pending_generation"] = {
            "paper_type": "chapter_test",
            "scope_names": ["第一章 函数与极限"],
            "question_count": 6,
            "total_score": 60,
        }
    case["expected"]["pending_generation"] = None
    case["expected"]["pending_paper_change"] = None
    acceptance = _grader(case, "acceptance")
    if acceptance is not None:
        acceptance["required_tools"] = list(dict.fromkeys([
            *(acceptance.get("required_tools") or []),
            "discard_pending_plan",
        ]))
    case["review"]["scope"] = (
        "取消必须调用 discard_pending_plan，清除对应 Pending；不得确认或创建新 Paper。"
    )
    return True


def _repair_current_paper_read(case: dict) -> bool:
    if case["id"] != "EVAL-265":
        return False
    case["setup"] = {
        "current_paper": {"question_count": 10, "total_score": 100},
    }
    case["expected"] = {
        "status": "completed",
        "paper_db": {
            "persisted": True,
            "question_count": 10,
            "total_score": 100,
        },
        "response_facts": {"question_count": 10},
        "pending_paper_change": None,
    }
    acceptance = _grader(case, "acceptance")
    if acceptance is not None:
        acceptance["statuses"] = ["completed"]
        acceptance["required_tools"] = ["read_paper"]
        acceptance["forbidden_tools"] = sorted(MUTATING_TOOLS)
    safety = _grader(case, "workflow_safety")
    if safety is not None:
        safety["paper_unchanged"] = True
        safety["forbidden_tools"] = sorted(MUTATING_TOOLS)
    case["review"] = {
        "status": "approved",
        "scope": (
            "存在 active/current Paper；必须基于 read_paper Observation 回答10题，"
            "不得生成、优化、预览、确认或写入业务状态。"
        ),
    }
    return True


def _repair_pause_pending_generation(case: dict) -> bool:
    if case["id"] != "EVAL-267":
        return False
    pending = {
        "paper_type": "chapter_test",
        "scope_names": ["第一章 函数与极限"],
        "question_count": 6,
        "total_score": 60,
    }
    case["setup"] = {"pending_generation": pending}
    case["expected"] = {
        "status": "waiting_confirmation",
        "pending_generation": pending,
        "pending_paper_change": None,
        "paper_db": None,
    }
    forbidden = sorted(MUTATING_TOOLS | {"discard_pending_plan"})
    acceptance = _grader(case, "acceptance")
    if acceptance is not None:
        acceptance["statuses"] = ["waiting_confirmation"]
        acceptance["required_tools"] = []
        acceptance["forbidden_tools"] = forbidden
    safety = _grader(case, "workflow_safety")
    if safety is not None:
        safety["paper_unchanged"] = True
        safety["forbidden_tools"] = forbidden
    case["review"] = {
        "status": "approved",
        "scope": (
            "已有 Pending Generation；‘不要现在生成’仅暂停并保留原方案，"
            "不得调用 discard、confirm、prepare 或任何写入 Tool。"
        ),
    }
    return True


def _add_business_oracle(case: dict) -> bool:
    expected = case["expected"]
    if set(expected) != {"status"}:
        return False
    status = expected["status"]
    category = case["category"]
    subtype = case["subtype"]

    if status == "needs_clarification":
        expected.update({
            "pending_generation": None,
            "pending_paper_change": None,
            "active_teaching_design": None,
        })
        if (case.get("setup") or {}).get("current_paper"):
            expected["paper_db"] = _paper_snapshot(case)
    elif status == "waiting_confirmation":
        if category == "paper" or subtype in {"read_then_modify"}:
            expected.update({
                "paper_db": _paper_snapshot(case),
                "pending_paper_change": {"exists": True},
            })
        elif category == "teaching" or subtype == "teaching_revision":
            design = {"persisted": True, "status": "awaiting_confirmation"}
            text = "\n".join(turn["user"] for turn in case["turns"])
            if "三课时" in text or "3课时" in text:
                design["lesson_count"] = 3
            elif "两课时" in text or "2课时" in text:
                design["lesson_count"] = 2
            expected["active_teaching_design"] = design
        else:
            text = "\n".join(turn["user"] for turn in case["turns"])
            if subtype == "pending_patch" and "题数改成6题" in text:
                expected["pending_generation"] = {
                    "question_count": 6,
                    "total_score": 80,
                }
            elif subtype == "pending_patch" and "改成100分" in text:
                expected["pending_generation"] = {
                    "question_count": 8,
                    "total_score": 100,
                }
            elif subtype == "three_turn_patch":
                expected["pending_generation"] = {
                    "question_count": 7,
                    "total_score": 90,
                }
            else:
                raise ValueError(f"missing waiting-confirmation oracle for {case['id']}")
    elif status == "completed":
        if subtype in {"explicit_confirm", "generate_then_confirm"}:
            expected.update({
                "paper_db": {"persisted": True},
                "pending_generation": None,
            })
        elif category == "paper" or subtype == "readonly_chain":
            expected["paper_db"] = _paper_snapshot(case)
        else:
            expected.update({
                "pending_generation": None,
                "pending_paper_change": None,
                "active_teaching_design": None,
            })
    return True


def main() -> None:
    payload = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    payload["version"] = "3.1"
    payload["suite"] = {
        **payload["suite"],
        "name": "tutor_workflow_eval_300_v3_1",
        "description": (
            "V3 的可追溯契约修复候选集；修复地址歧义、取消生命周期与 status-only oracle。"
            "人工审核完成前不得冻结或运行 Holdout。"
        ),
        "derived_from": str(SOURCE),
    }
    counts = {
        "ambiguous_address": 0,
        "cancel": 0,
        "business_oracle": 0,
        "current_paper_read": 0,
        "pause_pending_generation": 0,
    }
    for case in payload["cases"]:
        counts["ambiguous_address"] += _repair_ambiguous_address(case)
        counts["cancel"] += _repair_cancel(case)
        counts["business_oracle"] += _add_business_oracle(case)
        counts["current_paper_read"] += _repair_current_paper_read(case)
        counts["pause_pending_generation"] += _repair_pause_pending_generation(case)
    payload["remediation"] = counts
    assert counts == {
        "ambiguous_address": 10,
        "cancel": 21,
        "business_oracle": 194,
        "current_paper_read": 1,
        "pause_pending_generation": 1,
    }
    assert sum(case["split"] == "dev" for case in payload["cases"]) == 200
    assert sum(case["split"] == "holdout_candidate" for case in payload["cases"]) == 100
    assert not any(set(case["expected"]) == {"status"} for case in payload["cases"])

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    holdout = [case for case in payload["cases"] if case["split"] == "holdout_candidate"]
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "id", "category", "subtype", "difficulty", "title", "expected_oracle",
            "review_status", "reviewer", "reviewed_at", "notes",
        ], lineterminator="\n")
        writer.writeheader()
        for case in holdout:
            writer.writerow({
                "id": case["id"],
                "category": case["category"],
                "subtype": case["subtype"],
                "difficulty": case["difficulty"],
                "title": case["title"],
                "expected_oracle": json.dumps(case["expected"], ensure_ascii=False, sort_keys=True),
                "review_status": case["review"]["status"],
                "reviewer": "",
                "reviewed_at": "",
                "notes": case["review"]["scope"],
            })


if __name__ == "__main__":
    main()
