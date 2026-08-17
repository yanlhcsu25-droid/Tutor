"""Deterministic migration: re-link QuestionKnowledgeLink from orphaned KnowledgeNode to current directory KN.

Background
----------
After a taxonomy replace (``import_textbook_directory`` with ``replace=True``),
old ``source_type='textbook_directory'`` KnowledgeNodes were not passed to
``retire_directory_knowledge_nodes`` because the replace logic only queried
``source_type == 'directory'``.  Their ``curriculum_node_id`` became dangling
(指向已删除的 CurriculumNode), while ``QuestionKnowledgeLink`` rows still
reference the old KN IDs.

This script deterministically re-links affected ``QuestionKnowledgeLink`` rows
to the corresponding new ``source_type='directory'`` KnowledgeNode by exact
name match — no LLM, no fuzzy matching.

Usage
-----
    uv run python scripts/migrate_orphaned_knowledge_links.py --preview   # read-only
    uv run python scripts/migrate_orphaned_knowledge_links.py --confirm   # execute migration

Safety
------
- Preview is 100% read-only.
- Confirm runs in a single transaction; any error rolls back.
- Only 1:1 exact name matches are migrated; 0-match or multi-match → needs_manual_review.
- Mixed-link questions (already linked to the new KN) → old orphaned link is deleted, not duplicated.
- ``knowledge_match_status`` stays ``current`` for safe mappings (semantics unchanged).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from calculus_agent.config import get_settings
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionKnowledgeLink,
)


# ── Data structures ─────────────────────────────────────────────

@dataclass
class KnMapping:
    old_kn_id: str
    old_kn_name: str
    old_source_type: str
    affected_questions: int
    affected_links: int
    status: str = ""  # "safe" | "needs_manual_review"; set after match resolution
    new_kn_id: str | None = None
    new_kn_name: str | None = None
    new_source_type: str | None = None
    new_curriculum_code: str | None = None
    new_curriculum_title: str | None = None
    reason: str = ""


@dataclass
class MigrationPreview:
    orphaned_kn_total: int = 0
    orphaned_kn_with_questions: int = 0
    safe_mappings: int = 0
    unmatched_mappings: int = 0
    ambiguous_mappings: int = 0
    affected_questions: int = 0
    affected_links: int = 0
    mixed_questions: int = 0
    expected_updates: int = 0
    expected_deletes: int = 0
    mappings: list[KnMapping] = field(default_factory=list)


# ── Orphaned KN detection ───────────────────────────────────────

def _is_orphaned(kn: KnowledgeNode, valid_curriculum_ids: set[str]) -> bool:
    """Referential check: curriculum_node_id is set but points to a deleted node."""
    if kn.curriculum_node_id is None:
        return False
    return kn.curriculum_node_id not in valid_curriculum_ids


# ── Mapping builder ─────────────────────────────────────────────

def build_mapping(session, valid_curriculum_ids: set[str]) -> MigrationPreview:
    """Build old→new KN mapping. 100% read-only."""

    # All orphaned KNs (curriculum_node_id set but pointing to deleted node)
    all_kns = list(session.scalars(
        select(KnowledgeNode).where(KnowledgeNode.curriculum_node_id.is_not(None))
    ).all())
    orphaned_kns = [kn for kn in all_kns if _is_orphaned(kn, valid_curriculum_ids)]

    # Candidate new KNs: source_type='directory', valid curriculum_node_id
    new_kns_by_norm: dict[str, list[KnowledgeNode]] = {}
    for kn in session.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.source_type == "directory",
            KnowledgeNode.curriculum_node_id.is_not(None),
        )
    ).all():
        norm = normalize_name(kn.name)
        new_kns_by_norm.setdefault(norm, []).append(kn)

    # Curriculum node lookup for display — fetch all (small table)
    curriculum_map: dict[str, CurriculumNode] = {
        cn.id: cn
        for cn in session.scalars(select(CurriculumNode)).all()
    }

    preview = MigrationPreview()
    preview.orphaned_kn_total = len(orphaned_kns)

    for old_kn in orphaned_kns:
        # Count affected questions/links
        affected_links = session.scalar(
            select(func.count(QuestionKnowledgeLink.id)).where(
                QuestionKnowledgeLink.knowledge_node_id == old_kn.id
            )
        ) or 0
        affected_questions = session.scalar(
            select(func.count(func.distinct(QuestionKnowledgeLink.question_id))).where(
                QuestionKnowledgeLink.knowledge_node_id == old_kn.id
            )
        ) or 0

        if affected_questions == 0:
            continue  # Skip orphaned KNs with no question references

        preview.orphaned_kn_with_questions += 1

        # Try to find unique new KN by normalized name
        norm = normalize_name(old_kn.name)
        candidates = new_kns_by_norm.get(norm, [])

        # Filter: must have valid curriculum_node_id
        candidates = [c for c in candidates if c.curriculum_node_id in valid_curriculum_ids]

        mapping = KnMapping(
            old_kn_id=old_kn.id,
            old_kn_name=old_kn.name,
            old_source_type=old_kn.source_type,
            affected_questions=affected_questions,
            affected_links=affected_links,
        )

        if len(candidates) == 0:
            mapping.status = "needs_manual_review"
            mapping.reason = "no name match in current taxonomy"
            preview.unmatched_mappings += 1
        elif len(candidates) > 1:
            mapping.status = "needs_manual_review"
            mapping.reason = f"multiple matches: {len(candidates)} new KNs with same name"
            preview.ambiguous_mappings += 1
        else:
            new_kn = candidates[0]
            cn = curriculum_map.get(new_kn.curriculum_node_id)
            mapping.new_kn_id = new_kn.id
            mapping.new_kn_name = new_kn.name
            mapping.new_source_type = new_kn.source_type
            mapping.new_curriculum_code = cn.code if cn else None
            mapping.new_curriculum_title = cn.title if cn else None
            mapping.status = "safe"
            preview.safe_mappings += 1

        preview.mappings.append(mapping)
        preview.affected_questions += affected_questions
        preview.affected_links += affected_links

    # Count mixed-link questions for safe mappings
    for m in preview.mappings:
        if m.status != "safe" or m.new_kn_id is None:
            continue
        # Questions that have BOTH the old orphaned KN and the new KN
        mixed = session.scalar(
            select(func.count(func.distinct(QuestionKnowledgeLink.question_id))).where(
                QuestionKnowledgeLink.knowledge_node_id == m.old_kn_id,
                QuestionKnowledgeLink.question_id.in_(
                    select(QuestionKnowledgeLink.question_id).where(
                        QuestionKnowledgeLink.knowledge_node_id == m.new_kn_id
                    )
                ),
            )
        ) or 0
        preview.mixed_questions += mixed
        # For mixed questions: each old link that has a matching (question_id, relation_type) new link → delete
        # For non-mixed: UPDATE old link to new KN
        preview.expected_updates += m.affected_links
        # We'll compute exact deletes during confirm, but estimate here
        preview.expected_deletes += 0  # will be refined

    # Refine expected_updates vs deletes by checking actual duplicates
    total_updates = 0
    total_deletes = 0
    for m in preview.mappings:
        if m.status != "safe" or m.new_kn_id is None:
            continue
        old_links = list(session.scalars(
            select(QuestionKnowledgeLink).where(
                QuestionKnowledgeLink.knowledge_node_id == m.old_kn_id
            )
        ).all())
        for link in old_links:
            existing = session.scalar(
                select(QuestionKnowledgeLink).where(
                    QuestionKnowledgeLink.question_id == link.question_id,
                    QuestionKnowledgeLink.knowledge_node_id == m.new_kn_id,
                    QuestionKnowledgeLink.relation_type == link.relation_type,
                )
            )
            if existing:
                total_deletes += 1
            else:
                total_updates += 1
    preview.expected_updates = total_updates
    preview.expected_deletes = total_deletes

    # Globally distinct affected questions (per-KN counts above double-count
    # questions linked to multiple orphaned KNs).
    if preview.mappings:
        all_old_ids = [m.old_kn_id for m in preview.mappings]
        preview.affected_questions = session.scalar(
            select(func.count(func.distinct(QuestionKnowledgeLink.question_id))).where(
                QuestionKnowledgeLink.knowledge_node_id.in_(all_old_ids)
            )
        ) or 0

    return preview


# ── Preview printing ────────────────────────────────────────────

def print_preview(preview: MigrationPreview) -> None:
    print("=" * 80)
    print("  ORPHANED KNOWLEDGE LINK MIGRATION — PREVIEW")
    print("=" * 80)
    print()
    print("─" * 80)
    print("  STATISTICS")
    print("─" * 80)
    print(f"  Orphaned KN total (curriculum_node_id dangling):   {preview.orphaned_kn_total}")
    print(f"  Orphaned KN with question references:              {preview.orphaned_kn_with_questions}")
    print(f"  Safe unique mappings (1:1 exact name match):       {preview.safe_mappings}")
    print(f"  Unmatched (0 candidates):                          {preview.unmatched_mappings}")
    print(f"  Ambiguous (>1 candidates):                         {preview.ambiguous_mappings}")
    print()
    print(f"  Affected questions:                                {preview.affected_questions}")
    print(f"  Affected links:                                    {preview.affected_links}")
    print(f"  Mixed-link questions (old+new coexist):            {preview.mixed_questions}")
    print(f"  Expected UPDATE (old link → new KN):               {preview.expected_updates}")
    print(f"  Expected DELETE (duplicate old link removed):      {preview.expected_deletes}")
    print()
    print("─" * 80)
    print("  MAPPING TABLE")
    print("─" * 80)

    safe = [m for m in preview.mappings if m.status == "safe"]
    review = [m for m in preview.mappings if m.status == "needs_manual_review"]

    if safe:
        print()
        print(f"  SAFE MAPPINGS ({len(safe)}):")
        print()
        header = f"  {'OLD KN':<40} {'NEW KN':<40} {'Q':>4}  {'CURRICULUM':<20}  STATUS"
        print(header)
        print(f"  {'─'*40} {'─'*40} {'─'*4}  {'─'*20}  {'─'*15}")
        for m in sorted(safe, key=lambda x: x.affected_questions, reverse=True):
            old_name = m.old_kn_name[:38]
            new_name = (m.new_kn_name or "")[:38]
            curr = f"{m.new_curriculum_code or ''} {m.new_curriculum_title or ''}"[:20]
            print(f"  {old_name:<40} {new_name:<40} {m.affected_questions:>4}  {curr:<20}  safe")

    if review:
        print()
        print(f"  NEEDS MANUAL REVIEW ({len(review)}):")
        print()
        for m in sorted(review, key=lambda x: x.affected_questions, reverse=True):
            print(f"  {m.old_kn_name:<40}  Q={m.affected_questions:>3}  reason: {m.reason}")

    # Unmatched active questions (no knowledge links at all)
    print()
    print("─" * 80)
    print("  ACTIVE UNMATCHED QUESTIONS (not processed this phase)")
    print("─" * 80)

    print()
    print("=" * 80)
    if preview.unmatched_mappings == 0 and preview.ambiguous_mappings == 0:
        print("  RESULT: SAFE TO MIGRATE")
        print(f"  All {preview.orphaned_kn_with_questions} orphaned KNs with question references")
        print(f"  have unique exact name matches in the current taxonomy.")
    else:
        print("  RESULT: NOT SAFE TO MIGRATE")
        print(f"  {preview.unmatched_mappings} unmatched + {preview.ambiguous_mappings} ambiguous mappings found.")
        print("  Review the table above before proceeding.")
    print("=" * 80)


# ── Confirm (execute migration) ─────────────────────────────────

def execute_migration(session, preview: MigrationPreview) -> dict:
    """Execute migration in the current session/transaction.

    Caller is responsible for commit/rollback.
    Returns a summary dict.
    """
    safe_mappings = [m for m in preview.mappings if m.status == "safe" and m.new_kn_id]

    updated = 0
    deleted = 0

    for m in safe_mappings:
        old_links = list(session.scalars(
            select(QuestionKnowledgeLink).where(
                QuestionKnowledgeLink.knowledge_node_id == m.old_kn_id
            )
        ).all())

        for link in old_links:
            existing = session.scalar(
                select(QuestionKnowledgeLink).where(
                    QuestionKnowledgeLink.question_id == link.question_id,
                    QuestionKnowledgeLink.knowledge_node_id == m.new_kn_id,
                    QuestionKnowledgeLink.relation_type == link.relation_type,
                )
            )
            if existing:
                # Target link already exists → delete old orphaned link
                session.delete(link)
                deleted += 1
            else:
                # UPDATE old link → new KN
                link.knowledge_node_id = m.new_kn_id
                updated += 1

    session.flush()

    # Post-condition validation
    valid_curriculum_ids = {
        cn.id for cn in session.scalars(select(CurriculumNode)).all()
    }
    linked_orphaned = session.scalar(
        select(func.count(func.distinct(KnowledgeNode.id))).where(
            KnowledgeNode.curriculum_node_id.is_not(None),
            ~KnowledgeNode.curriculum_node_id.in_(valid_curriculum_ids),
            KnowledgeNode.id.in_(
                select(QuestionKnowledgeLink.knowledge_node_id)
            ),
        )
    ) or 0

    duplicate_subq = (
        select(
            QuestionKnowledgeLink.question_id,
            QuestionKnowledgeLink.knowledge_node_id,
            QuestionKnowledgeLink.relation_type,
        )
        .group_by(
            QuestionKnowledgeLink.question_id,
            QuestionKnowledgeLink.knowledge_node_id,
            QuestionKnowledgeLink.relation_type,
        )
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_links = session.scalar(
        select(func.count()).select_from(duplicate_subq)
    ) or 0

    return {
        "updated": updated,
        "deleted": deleted,
        "linked_orphaned_kn": linked_orphaned,
        "duplicate_links": duplicate_links,
    }


# ── Main ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate orphaned QuestionKnowledgeLink to current directory KnowledgeNodes."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview", action="store_true", help="Read-only preview (no writes)")
    group.add_argument("--confirm", action="store_true", help="Execute migration")
    args = parser.parse_args()

    settings = get_settings()
    create_schema(settings.database_url)
    factory = build_session_factory(settings.database_url)

    if args.preview:
        with factory() as session:
            valid_curriculum_ids = {
                cn.id for cn in session.scalars(select(CurriculumNode)).all()
            }
            preview = build_mapping(session, valid_curriculum_ids)
            print_preview(preview)
        return

    if args.confirm:
        with factory() as session:
            valid_curriculum_ids = {
                cn.id for cn in session.scalars(select(CurriculumNode)).all()
            }
            preview = build_mapping(session, valid_curriculum_ids)

            if preview.unmatched_mappings > 0 or preview.ambiguous_mappings > 0:
                print("ABORT: Cannot migrate with unmatched or ambiguous mappings.")
                print(f"  unmatched={preview.unmatched_mappings}, ambiguous={preview.ambiguous_mappings}")
                print("  Run --preview for details.")
                return

            print("Executing migration...")
            result = execute_migration(session, preview)

            if result["linked_orphaned_kn"] != 0:
                session.rollback()
                print("FAILED: Post-condition violated — linked_orphaned_kn != 0")
                print(f"  linked_orphaned_kn={result['linked_orphaned_kn']}")
                print("  Transaction rolled back.")
                return

            if result["duplicate_links"] != 0:
                session.rollback()
                print("FAILED: Post-condition violated — duplicate_links != 0")
                print(f"  duplicate_links={result['duplicate_links']}")
                print("  Transaction rolled back.")
                return

            session.commit()
            print("Migration completed successfully.")
            print(f"  Links updated:  {result['updated']}")
            print(f"  Links deleted:  {result['deleted']}")
            print(f"  Post-condition: linked_orphaned_kn={result['linked_orphaned_kn']}, "
                  f"duplicate_links={result['duplicate_links']}")
        return


if __name__ == "__main__":
    main()
