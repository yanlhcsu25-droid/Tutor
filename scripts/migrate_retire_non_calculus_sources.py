"""一次性数据迁移：把全部 CMM-Math 与 built-in-demo 来源的 Question 软下架。

背景
----
经溯源确认（见变更说明）：
- CMM-Math 数据由 `POST /api/v1/datasets/cmm-math/import` 手动导入，全是初中数学题
  （七年级期末 / 因式分解 / 超市促销应用题等），不属于微积分教学内容。
- built-in-demo 由 `scripts/setup.sh` 装机时 `python -m calculus_agent.demo` 自动 seed，
  15 道全是初中数学（一次函数 / 整式运算 / 三角形内角和 …）。

这两类题即使题型合法，也严重偏离「微积分」知识域，且 `selector._candidates` 早已用
`EXCLUDED_PAPER_SOURCE_NAMES = {"CMM-Math","test_source","built-in-demo"}` 把它们挡在候选池外。
但保留 1822 道 CMM + 10 道 demo 的 active 题在日常统计、审核队列、知识点匹配里仍是噪声，
故按用户要求将其全部软下架（is_active=0 + knowledge_match_status="retired"），
与 `retire_formal_question` 语义一致，可回滚。

幂等性
------
只处理 is_active=1 的行，重复执行为 no-op。

`--purge-drafts` 选项
---------------------
默认只软下架 Question 行，保留 question_draft 快照（可溯源、可回滚）。
若用户「全部删除」意图包含一并清空草稿，可加 `--purge-drafts` 把对应 draft 也物理删除。
该开关需显式传入，默认不删草稿。

用法
----
    uv run python scripts/migrate_retire_non_calculus_sources.py --dry-run
    uv run python scripts/migrate_retire_non_calculus_sources.py --apply
    uv run python scripts/migrate_retire_non_calculus_sources.py --apply --purge-drafts
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from calculus_agent.db import create_schema, session_scope
from calculus_agent.models import Question, QuestionDraft

DATABASE_URL = "sqlite:///./calculus_agent.db"

# 要全量软下架的题源（draft.source_name）
TARGET_SOURCES = ("CMM-Math", "built-in-demo")


def _status_breakdown(session, src: str) -> dict:
    rows = session.execute(
        select(Question.is_active, Question.knowledge_match_status)
        .join(QuestionDraft, Question.draft_id == QuestionDraft.id)
        .where(QuestionDraft.source_name == src)
    ).all()
    total = len(rows)
    active = sum(1 for r in rows if r[0] is True and r[1] == "current")
    retired = sum(1 for r in rows if r[0] is False)
    return {"total": total, "active": active, "retired": retired}


def run(apply: bool, purge_drafts: bool) -> int:
    # 真库可能尚未应用 question.updated_at 等列迁移（应用启动时才触发），先补齐。
    create_schema(DATABASE_URL)

    with session_scope(DATABASE_URL) as session:
        print("=== 迁移前 · 目标来源 Question 状态 ===")
        for src in TARGET_SOURCES:
            b = _status_breakdown(session, src)
            print(f"  {src:<14} total={b['total']:>5}  active={b['active']:>5}  retired={b['retired']:>5}")

        # ---- 软下架 ----
        print("\n=== 软下架（is_active=0 + knowledge_match_status='retired'）===")
        joined = session.execute(
            select(Question, QuestionDraft.source_name)
            .join(QuestionDraft, Question.draft_id == QuestionDraft.id)
            .where(QuestionDraft.source_name.in_(TARGET_SOURCES))
            .where(Question.is_active.is_(True))
        ).all()
        to_retire = [row[0] for row in joined]
        by_src = Counter(row[1] for row in joined)
        for src, count in sorted(by_src.items(), key=lambda kv: -kv[1]):
            print(f"  [{'将下架' if apply else '待下架'}] {src:<14} {count} 道")
        if apply:
            for q in to_retire:
                q.is_active = False
                q.knowledge_match_status = "retired"
            session.flush()
        print(f"  合计 {'已下架' if apply else '待下架'}: {len(to_retire)} 道")

        # ---- 可选：清空对应草稿 ----
        if purge_drafts:
            print("\n=== 物理删除对应 question_draft（--purge-drafts）===")
            drafts = session.execute(
                select(QuestionDraft).where(QuestionDraft.source_name.in_(TARGET_SOURCES))
            ).all()
            removable = []
            for d in drafts:
                still_active = session.execute(
                    select(Question.id)
                    .where(Question.draft_id == d.id)
                    .where(Question.is_active.is_(True))
                ).first()
                if still_active is None:
                    removable.append(d)
            for d in removable:
                print(f"  [{'已删' if apply else '待删'}] draft {d.id} ({d.source_name})")
                if apply:
                    session.delete(d)
            print(f"  合计 {'已删' if apply else '待删'}: {len(removable)} 条 draft")
            if apply:
                session.flush()

        # ---- 迁移后复核 ----
        print("\n=== 迁移后 · 目标来源 Question 状态 ===")
        for src in TARGET_SOURCES:
            b = _status_breakdown(session, src)
            print(f"  {src:<14} total={b['total']:>5}  active={b['active']:>5}  retired={b['retired']:>5}")

        if apply:
            session.flush()
            print("\n[apply] 已落库。")
        else:
            print("\n[dry-run] 未写库。加 --apply 落库。")
            session.rollback()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    group.add_argument("--apply", action="store_true", help="执行迁移并写库")
    parser.add_argument("--purge-drafts", action="store_true",
                        help="额外物理删除对应 question_draft（默认保留快照）")
    args = parser.parse_args()
    return run(apply=args.apply, purge_drafts=args.purge_drafts)


if __name__ == "__main__":
    raise SystemExit(main())
