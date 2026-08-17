"""Deterministic migration of historical ``question_type`` values to the canonical contract.

Canonical question types (the only allowed values):
    {"选择题", "填空题", "计算题", "证明题", "unknown"}

This script rewrites the ``question_type`` column of ``question`` and
``question_draft`` so that every row conforms. It is:

  * transactional  — all changes commit or roll back together;
  * idempotent     — re-running changes nothing once canonical;
  * safe           — does NOT delete rows, change question ids, knowledge
                     links, difficulty, answers, or source/revision metadata.

Usage:
    python scripts/migrate_question_types.py --preview
    python scripts/migrate_question_types.py --confirm
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.db import build_session_factory
from calculus_agent.models import Question, QuestionDraft
from calculus_agent.question_types import (
    VALID_QUESTION_TYPES,
    canonical_question_type,
)


def _table_stats(session: Session, model) -> dict[str, int]:
    rows = session.execute(
        select(model.question_type, func.count(model.id))
        .group_by(model.question_type)
        .order_by(func.count(model.id).desc())
    ).all()
    return {str(t): c for t, c in rows}


def _preview_table(name: str, stats: dict[str, int]) -> dict:
    affected = 0
    unchanged = 0
    unknown_after = 0
    print(f"\n=== {name} ===")
    for raw, count in stats.items():
        canon = canonical_question_type(raw)
        marker = "" if canon == raw else "  -> " + canon
        if canon == raw:
            unchanged += count
        else:
            affected += count
        if canon == "unknown":
            unknown_after += count
        print(f"  {raw!r:>18}: {count:>5}{marker}")
    return {
        "affected": affected,
        "unchanged": unchanged,
        "unknown_after": unknown_after,
    }


def build_preview(session: Session) -> dict:
    q_stats = _table_stats(session, Question)
    d_stats = _table_stats(session, QuestionDraft)
    q = _preview_table("question", q_stats)
    d = _preview_table("question_draft", d_stats)
    total_q = sum(q_stats.values())
    total_d = sum(d_stats.values())
    print("\n=== summary ===")
    print(f"  question      : total={total_q}  affected={q['affected']}  "
          f"unchanged={q['unchanged']}  unknown_after={q['unknown_after']}")
    print(f"  question_draft: total={total_d}  affected={d['affected']}  "
          f"unchanged={d['unchanged']}  unknown_after={d['unknown_after']}")
    return {"question": q, "question_draft": d}


def _migrate_table(session: Session, model) -> int:
    rows = session.scalars(select(model)).all()
    changed = 0
    for row in rows:
        canon = canonical_question_type(row.question_type)
        if canon != row.question_type:
            row.question_type = canon  # @validates re-canonicalizes (idempotent)
            changed += 1
    return changed


def execute_migration(session: Session) -> dict:
    q_changed = _migrate_table(session, Question)
    d_changed = _migrate_table(session, QuestionDraft)

    # Post-condition: every distinct question_type must be canonical.
    bad_q = session.scalars(
        select(Question.question_type).distinct()
    ).all()
    bad_d = session.scalars(
        select(QuestionDraft.question_type).distinct()
    ).all()
    illegal = [
        t for t in list(bad_q) + list(bad_d)
        if t not in VALID_QUESTION_TYPES
    ]
    if illegal:
        raise RuntimeError(
            f"post-condition violated: non-canonical types remain: {illegal}"
        )
    return {"question_changed": q_changed, "question_draft_changed": d_changed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview", action="store_true", help="print the planned migration (read-only)")
    group.add_argument("--confirm", action="store_true", help="apply the migration")
    args = parser.parse_args()

    factory = build_session_factory(get_settings().database_url)
    with factory() as session:
        if args.preview:
            build_preview(session)
            return 0

        # --confirm
        preview = build_preview(session)
        result = execute_migration(session)
        session.commit()
        print("\n=== confirm result ===")
        print(f"  question rows changed     : {result['question_changed']}")
        print(f"  question_draft rows changed: {result['question_draft_changed']}")
        print("  committed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
