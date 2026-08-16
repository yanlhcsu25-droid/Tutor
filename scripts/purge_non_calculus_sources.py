"""物理删除 CMM-Math 与 built-in-demo 来源的全部业务数据（硬删除，不可逆）。

背景
----
上一轮对这两类来源做了 soft-retire（is_active=0 + retired），但用户确认要的是
**彻底物理删除**，不是软下架。这两类数据全是初中数学，非微积分内容，且候选池早已用
`EXCLUDED_PAPER_SOURCE_NAMES` 排除。

依赖探查结论（PRAGMA foreign_keys=0，无声明式外键，需手动按序删）：
- `question`              : draft_id -> question_draft.id
- `paper_item`            : question_id -> question.id          （2 行引用了 CMM/demo 题）
- `question_knowledge_link`: question_id -> question.id         （3091 行）
- `question_knowledge_review`: question_id -> question.id       （0 行，no-op）
- `question_profile`      : question_id -> question.id          （0 行，no-op）
- `question_draft`        : 源头，按 source_name 删除

删除顺序（单事务，先子后父，避免孤儿）：
  paper_item -> question_knowledge_link -> question_knowledge_review
  -> question_profile -> question -> question_draft

`mistake_prep_task.matched_question_ids_json` / `constraint_violation.question_ids_json`
仅以 JSON 文本内嵌 id（非 question_id/draft_id 列），不在本脚本范围内（按用户口径
"通过 question_id/draft_id 关联的表" 不包含它们）。

用法
----
    uv run python scripts/purge_non_calculus_sources.py --dry-run
    uv run python scripts/purge_non_calculus_sources.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_PATH = str(Path(__file__).resolve().parents[1] / "calculus_agent.db")
TARGET_SOURCES = ("CMM-Math", "built-in-demo")


def run(apply: bool) -> int:
    con = sqlite3.connect(DATABASE_PATH)
    con.execute("PRAGMA foreign_keys = OFF")  # 本库默认即 OFF，显式关闭以允许手动按序删
    cur = con.cursor()

    # 目标 question id 子查询
    qids_sub = """
        SELECT q.id FROM question q
        JOIN question_draft d ON d.id = q.draft_id
        WHERE d.source_name IN ('CMM-Math', 'built-in-demo')
    """

    # ---- dry-run：先报数 ----
    print("=== 待删除行数（dry-run 预览）===")
    counts = {}
    counts["question"] = cur.execute(
        f"SELECT COUNT(*) FROM question q JOIN question_draft d ON d.id=q.draft_id "
        f"WHERE d.source_name IN ('CMM-Math','built-in-demo')"
    ).fetchone()[0]
    counts["question_draft"] = cur.execute(
        "SELECT COUNT(*) FROM question_draft WHERE source_name IN ('CMM-Math','built-in-demo')"
    ).fetchone()[0]
    counts["paper_item"] = cur.execute(
        f"SELECT COUNT(*) FROM paper_item WHERE question_id IN ({qids_sub})"
    ).fetchone()[0]
    counts["question_knowledge_link"] = cur.execute(
        f"SELECT COUNT(*) FROM question_knowledge_link WHERE question_id IN ({qids_sub})"
    ).fetchone()[0]
    counts["question_knowledge_review"] = cur.execute(
        f"SELECT COUNT(*) FROM question_knowledge_review WHERE question_id IN ({qids_sub})"
    ).fetchone()[0]
    counts["question_profile"] = cur.execute(
        f"SELECT COUNT(*) FROM question_profile WHERE question_id IN ({qids_sub})"
    ).fetchone()[0]
    for t, n in counts.items():
        print(f"  {t:<26} {n}")

    if not apply:
        print("\n[dry-run] 未写库。加 --apply 落库。")
        con.close()
        return 0

    # ---- apply：单事务物理删除 ----
    print("\n=== 执行物理删除（单事务）===")
    cur.execute("BEGIN")
    try:
        n_paper = cur.execute(f"DELETE FROM paper_item WHERE question_id IN ({qids_sub})").rowcount
        n_link = cur.execute(f"DELETE FROM question_knowledge_link WHERE question_id IN ({qids_sub})").rowcount
        n_rev = cur.execute(f"DELETE FROM question_knowledge_review WHERE question_id IN ({qids_sub})").rowcount
        n_prof = cur.execute(f"DELETE FROM question_profile WHERE question_id IN ({qids_sub})").rowcount
        n_q = cur.execute(f"DELETE FROM question WHERE id IN ({qids_sub})").rowcount
        n_d = cur.execute(
            "DELETE FROM question_draft WHERE source_name IN ('CMM-Math','built-in-demo')"
        ).rowcount
        con.commit()
    except Exception as e:
        con.rollback()
        print(f"  [ERROR] 删除失败，已回滚：{e}")
        con.close()
        return 1

    print(f"  paper_item            删 {n_paper}")
    print(f"  question_knowledge_link 删 {n_link}")
    print(f"  question_knowledge_review 删 {n_rev}")
    print(f"  question_profile      删 {n_prof}")
    print(f"  question              删 {n_q}")
    print(f"  question_draft        删 {n_d}")
    con.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    group.add_argument("--apply", action="store_true", help="执行物理删除并写库")
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
