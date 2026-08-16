"""一次性数据迁移：废除「解答题」canonical 题型后的历史数据清理。

背景
----
`解答题` 已从 ALLOWED_QUESTION_TYPES 移除，`unknown` 不再自动映射为任何正式题型。
组卷候选池（selector._candidates）现按 canonical 题型过滤，非法题型一律排除。
本脚本把历史脏数据一次性收敛到新契约上。

两类操作
--------
1. 题型修正（12 道，approved+active，逐题人工判定）
   走应用层 `patch_question_type_value`：严格校验 canonical、同步 question_draft、
   保持审核状态、写 updated_at。等价于教师在题库抽屉里手工改题型。

2. 下架（899 道 `解答题`，全部 is_active=1）
   复用 `retire_formal_question` 的语义：is_active=False + knowledge_match_status="retired"。
   - 5 道 approved：初中数学 demo 种子（解一元一次方程 / 一次函数 / 三角形内角和），非微积分内容。
   - 894 道 pending：publish_source=manual / verification_status=dataset_reference，
     全是初中数学数据集参考题（七年级期末、因式分解、超市促销应用题）。

`unknown` 说明
--------------
自动规则层面 unknown 永远保持 unknown（question_types.py 已无 unknown 别名）。
这里只对 2 道内容明确的题做「人工定型」，等价于教师手工修正，不违反自动映射禁令。

幂等性
------
题型修正：目标题已是目标 canonical 时跳过。
下架：只处理 is_active=1 的行，重复执行为 no-op。

用法
----
    uv run python scripts/migrate_retire_subjective_types.py --dry-run   # 只报告
    uv run python scripts/migrate_retire_subjective_types.py --apply     # 落库
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from calculus_agent.api import patch_question_type_value
from calculus_agent.db import create_schema, session_scope
from calculus_agent.models import Question, QuestionDraft
from calculus_agent.question_types import ALLOWED_QUESTION_TYPES, canonical_question_type

DATABASE_URL = "sqlite:///./calculus_agent.db"

# 逐题人工判定结果：题干依据见 docs / 变更说明。
TYPE_FIXES: dict[str, str] = {
    # other(1) —— 「则在 (x1,xn) 内至少有一点 ξ 使 f(ξ)=平均值」→ 存在性证明
    "1d1ddf72-c7a7-4af2-a508-901bf54998f5": "证明题",
    # subjective(7) —— 「哪些对/错？说明理由；若错请给出反例」「试举出例子」→ 论证类
    "3221dce5-e6a8-4d5b-baa5-3c5b9d709684": "证明题",
    "537aea3a-357c-4a4c-a883-2a840401c282": "证明题",
    "82bb541b-acb2-4180-b3d3-92e25eeac098": "证明题",
    "90089db8-b84c-474e-b1fa-5de6f7e303ee": "证明题",
    "dd744d49-4f0b-4577-bb39-c9e20b157b2c": "证明题",
    "ecb5ed35-ca96-4200-b97e-c4242d9f89e2": "证明题",
    "fb4a8e91-3a27-4801-94ea-99d4ce265162": "证明题",
    # subjective(2) —— 「哪一个是高阶无穷小?」需比较无穷小阶数 → 计算
    "1dbe253f-aeba-4301-8708-cda89bbd4412": "计算题",
    "40921b8a-9dd7-4e1e-88a7-c77c5b077f72": "计算题",
    # unknown(1) —— 「讨论方程 x·e^{-x}=a 的根的个数」→ 计算/讨论
    "06b9f714-07cc-44c2-9c1c-3928494292b2": "计算题",
    # unknown(1) —— 题干带 "= _____" 明确填空
    "30bf6627-d4b0-4784-b8a0-ad3c87d8849e": "填空题",
}

RETIRE_TYPE = "解答题"


def _pool_snapshot(session) -> list[tuple[str, int]]:
    """候选池口径下的题型分布（与 selector._candidates 过滤条件一致）。"""
    rows = session.execute(
        select(Question.question_type)
        .where(Question.review_status == "approved")
        .where(Question.is_active.is_(True))
        .where(Question.knowledge_match_status == "current")
    ).all()
    counts: dict[str, int] = {}
    for (raw,) in rows:
        counts[raw] = counts.get(raw, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _report_pool(session, label: str) -> None:
    print(f"\n=== {label} ===")
    total_in_pool = 0
    for raw, count in _pool_snapshot(session):
        canonical = canonical_question_type(raw)
        allowed = canonical in ALLOWED_QUESTION_TYPES
        mark = "OK " if allowed else "排除"
        if allowed:
            total_in_pool += count
        print(f"  [{mark}] {raw:<12} -> {canonical:<8} {count}")
    print(f"  可入池合计: {total_in_pool}")


def run(apply: bool) -> int:
    # 真库可能尚未应用 question.updated_at 等列迁移（应用启动时才触发），
    # 这里先补齐 schema，否则 patch_question_type_value 会因缺列而失败。
    create_schema(DATABASE_URL)

    with session_scope(DATABASE_URL) as session:
        _report_pool(session, "迁移前 · 候选池题型分布")

        # ---- 1. 题型修正 ----
        print("\n=== 题型修正（走 patch_question_type_value）===")
        fixed = skipped = missing = 0
        for question_id, target in TYPE_FIXES.items():
            question = session.get(Question, question_id)
            if question is None or not question.is_active:
                print(f"  [缺失] {question_id} 不存在或已下架")
                missing += 1
                continue
            if question.question_type == target:
                print(f"  [跳过] {question_id} 已是 {target}")
                skipped += 1
                continue
            before = question.question_type
            if apply:
                patch_question_type_value(session, question_id, target)
            print(f"  [修正] {question_id} {before} -> {target}")
            fixed += 1

        # ---- 2. 下架历史「解答题」----
        print(f"\n=== 下架历史「{RETIRE_TYPE}」(is_active=1) ===")
        stale = session.scalars(
            select(Question)
            .where(Question.question_type == RETIRE_TYPE)
            .where(Question.is_active.is_(True))
        ).all()
        by_status: dict[str, int] = {}
        for question in stale:
            by_status[question.review_status] = by_status.get(question.review_status, 0) + 1
        for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
            print(f"  review_status={status:<10} {count} 道")
        if apply:
            for question in stale:
                question.is_active = False
                question.knowledge_match_status = "retired"
            session.flush()
        print(f"  {'已下架' if apply else '待下架'}: {len(stale)} 道")

        # ---- 3. draft 侧残留检查 ----
        draft_left = session.execute(
            select(QuestionDraft.question_type)
            .where(QuestionDraft.question_type == RETIRE_TYPE)
        ).all()
        print(f"\n  question_draft 中仍为「{RETIRE_TYPE}」: {len(draft_left)} 条"
              f"（草稿表为导入快照，不参与组卷，保留原值以便溯源）")

        if apply:
            session.flush()
            _report_pool(session, "迁移后 · 候选池题型分布")
        else:
            print("\n[dry-run] 未写库。加 --apply 落库。")
            session.rollback()

        print(f"\n题型修正: 修正 {fixed} / 跳过 {skipped} / 缺失 {missing}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    group.add_argument("--apply", action="store_true", help="执行迁移并写库")
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
