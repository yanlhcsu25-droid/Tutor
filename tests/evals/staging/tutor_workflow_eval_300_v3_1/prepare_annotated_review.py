"""Deduplicate the annotated queue and expand cases requiring context review."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

ROOT = Path("tests/evals/staging/tutor_workflow_eval_300_v3_1")
ANNOTATED = ROOT / "holdout_review_queue_v3_1_annotated.csv"
DATASET = ROOT / "tutor_workflow_eval_300_v3_1.yaml"
DEDUPED = ROOT / "holdout_review_queue_v3_1_deduplicated.csv"
DUPLICATES = ROOT / "holdout_duplicates_v3_1.csv"
CONTEXT_CSV = ROOT / "holdout_context_review_v3_1.csv"
CONTEXT_MD = Path("docs/holdout_context_review_v3_1.md")
CONTEXT_REVIEW = {"needs_setup_review", "needs_full_turn_review"}
REVIEW_FOCUS = {
    "EVAL-262": "当前无 setup。审核它应保持‘缺少预览对象→澄清’，还是补 pending 以测试‘预览但不确认’。",
    "EVAL-263": "当前无 pending setup。审核它是缺失指代澄清，还是应补 pending 并验证只读题数、禁止确认。",
    "EVAL-264": "当前无 pending generation。审核它应澄清修改对象，还是补 pending 并断言总分 patch=80。",
    "EVAL-265": "语句明确提到当前卷，但 setup 缺失。审核是否补 current_paper，并改为 completed + read_paper + response_facts.question_count。",
    "EVAL-267": "语句要求保留方案且暂不生成，但 setup 缺失。审核是否补 pending_generation，并断言 Pending 保留且不调用 discard/confirm。",
    "EVAL-268": "缺少被约束的组卷请求与题库供给上下文。审核是保留 standalone 澄清，还是改成有 pending 的多轮 shortage case。",
    "EVAL-269": "缺少当前章节与组卷方案。审核是保留 standalone 澄清，还是补 pending 并验证范围约束不被放宽。",
    "EVAL-270": "只有只读边界，没有读取对象或问题。审核保持澄清，或补 current_paper 及明确查询目标。",
    "EVAL-279": "完整 turns 已显示生成后确认；审核约束继承、真实 Paper 持久化以及 Pending 清理。",
    "EVAL-289": "与 EVAL-279 完整 turns/oracle 相同；审核后考虑标记 duplicate_of=EVAL-279。",
    "EVAL-299": "与 EVAL-279 完整 turns/oracle 相同；审核后考虑标记 duplicate_of=EVAL-279。",
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def main() -> None:
    with ANNOTATED.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    cases = {
        case["id"]: case
        for case in yaml.safe_load(DATASET.read_text(encoding="utf-8"))["cases"]
    }
    # User-reviewed correction: EVAL-265 now has a current Paper and a
    # read-only grounding oracle, so it no longer needs setup review.
    corrected = next(row for row in rows if row["id"] == "EVAL-265")
    corrected.update({
        "expected_oracle": _json(cases["EVAL-265"]["expected"]),
        "review_status": "approved",
        "reviewer": "user-confirmed",
        "reviewed_at": "2026-10-04T14:56:34Z",
        "notes": cases["EVAL-265"]["review"]["scope"],
        "recommended_review": "approve",
        "review_reason": (
            "已补 current_paper fixture；要求 read_paper 权威读取、回答10题，"
            "并禁止生成、优化、preview、confirm 和业务写入。"
        ),
    })
    corrected = next(row for row in rows if row["id"] == "EVAL-267")
    corrected.update({
        "expected_oracle": _json(cases["EVAL-267"]["expected"]),
        "review_status": "approved",
        "reviewer": "user-confirmed",
        "reviewed_at": "2026-10-04T14:56:34Z",
        "notes": cases["EVAL-267"]["review"]["scope"],
        "recommended_review": "approve",
        "review_reason": (
            "已补 pending_generation fixture；‘不要现在生成’必须保留 Pending，"
            "且不得调用 discard、confirm、prepare 或写入 Tool。"
        ),
    })
    corrected = next(row for row in rows if row["id"] == "EVAL-279")
    corrected.update({
        "expected_oracle": _json(cases["EVAL-279"]["expected"]),
        "review_status": "approved",
        "reviewer": "user-confirmed",
        "reviewed_at": "2026-10-04T14:56:34Z",
        "notes": cases["EVAL-279"]["review"]["scope"],
        "recommended_review": "approve",
        "review_reason": "最终 Paper oracle 已补强为6题60分，并要求清空 Pending。",
    })
    for row in rows:
        case = cases[row["id"]]
        if case["split"] == "duplicate_regression":
            row.update({
                "expected_oracle": _json(case["expected"]),
                "review_status": "duplicate",
                "reviewer": "user-confirmed",
                "reviewed_at": "2026-10-05T12:00:00Z",
                "notes": case["review"]["scope"],
                "recommended_review": "deduplicate",
                "review_reason": "已确认重复；保留到 duplicate regression，不计入主 Holdout。",
                "duplicate_of": case["duplicate_of"],
            })
        elif case["split"] == "holdout_candidate":
            row.update({
                "expected_oracle": _json(case["expected"]),
                "review_status": "approved",
                "reviewer": "user-confirmed",
                "reviewed_at": "2026-10-05T12:00:00Z",
                "notes": case["review"]["scope"],
                "recommended_review": "approve",
            })

    canonical = [row for row in rows if not row["duplicate_of"].strip()]
    duplicates = [row for row in rows if row["duplicate_of"].strip()]
    assert len(rows) == 100
    assert len(canonical) == 69
    assert len(duplicates) == 31
    assert all(row["duplicate_of"] in cases for row in duplicates)

    with DEDUPED.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(canonical)

    duplicate_fields = ["id", "duplicate_of", "category", "subtype", "title", "review_reason"]
    with DUPLICATES.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=duplicate_fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(duplicates)

    context_rows = []
    for row in canonical:
        if row["recommended_review"] not in CONTEXT_REVIEW:
            continue
        case = cases[row["id"]]
        acceptance = next(
            (grader for grader in case["graders"] if isinstance(grader, dict) and grader.get("type") == "acceptance"),
            {},
        )
        context_rows.append({
            "id": row["id"],
            "recommended_review": row["recommended_review"],
            "title": case["title"],
            "turns": _json(case["turns"]),
            "setup": _json(case.get("setup")),
            "expected": _json(case["expected"]),
            "required_tools": _json(acceptance.get("required_tools") or []),
            "forbidden_tools": _json(acceptance.get("forbidden_tools") or []),
            "review_focus": REVIEW_FOCUS[row["id"]],
            "review_decision": "",
            "reviewer_notes": "",
        })
    assert len(context_rows) == 0

    context_fields = [
        "id", "recommended_review", "title", "turns", "setup", "expected",
        "required_tools", "forbidden_tools", "review_focus", "review_decision",
        "reviewer_notes",
    ]
    with CONTEXT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=context_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(context_rows)

    lines = [
        "# Holdout V3.1 — 上下文复审已完成", "",
        "- 原始标注：100 条", "- `duplicate_of` 去重：31 条", "- 独立 Holdout：69 条",
        "- 人工 approved：69 条", "- Duplicate Regression：31 条",
        "- 剩余上下文复审：0 条", "",
        "> 本步骤只整理审核材料，不修改 V3.1 Dataset，也不自动批准 case。", "",
    ]
    for row in context_rows:
        case = cases[row["id"]]
        lines.extend([
            f"## {row['id']} — {case['title']}", "",
            f"- 标记：`{row['recommended_review']}`",
            f"- Category/Subtype：`{case['category']} / {case['subtype']}`",
            f"- Setup：`{row['setup']}`",
            f"- Expected：`{row['expected']}`",
            f"- Required tools：`{row['required_tools']}`",
            f"- Forbidden tools：`{row['forbidden_tools']}`",
            "- 完整 turns：",
        ])
        for index, turn in enumerate(case["turns"], 1):
            lines.append(f"  {index}. 用户：{turn['user']}")
        lines.extend([
            f"- **审核焦点**：{row['review_focus']}",
            "- 审核决定：`pending`", "",
        ])
    CONTEXT_MD.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
