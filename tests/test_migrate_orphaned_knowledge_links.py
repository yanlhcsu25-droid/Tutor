"""Tests for orphaned KnowledgeNode link migration.

Covers preview (read-only), mapping safety, link migration, mixed-link dedup,
transaction rollback, knowledge_match_status preservation, post-condition
validation, and taxonomy-replace regression.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)
from scripts.migrate_orphaned_knowledge_links import (
    build_mapping,
    execute_migration,
)


# ── Helpers ─────────────────────────────────────────────────────

def _make_textbook(session, name="高数上册") -> Textbook:
    book = Textbook(name=name, is_active=True)
    session.add(book)
    session.flush()
    return book


def _make_curriculum(session, book_id, code, title, parent_id=None) -> CurriculumNode:
    node = CurriculumNode(
        textbook_id=book_id,
        parent_id=parent_id,
        node_type="section",
        code=code,
        title=title,
        sort_order=0,
    )
    session.add(node)
    session.flush()
    return node


def _make_directory_kn(session, name, curriculum_id) -> KnowledgeNode:
    # Directory KNs use suffixed normalized_name to avoid UNIQUE collision
    # with legacy textbook_directory KNs that share the same plain name.
    # This mirrors _directory_normalized_name in curriculum.py.
    norm = normalize_name(name)
    collision = session.scalar(
        select(KnowledgeNode.id).where(
            KnowledgeNode.node_type == "concept",
            KnowledgeNode.normalized_name == norm,
        ).limit(1)
    )
    normalized = norm if collision is None else f"{norm}::{curriculum_id}"
    kn = KnowledgeNode(
        curriculum_node_id=curriculum_id,
        node_type="concept",
        name=name,
        normalized_name=normalized,
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(kn)
    session.flush()
    return kn


def _make_textbook_directory_kn(session, name, curriculum_id) -> KnowledgeNode:
    """Simulate a legacy textbook_directory KN (the orphaned kind)."""
    kn = KnowledgeNode(
        curriculum_node_id=curriculum_id,
        node_type="concept",
        name=name,
        normalized_name=normalize_name(name),
        source_type="textbook_directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(kn)
    session.flush()
    return kn


def _make_question(session, text="test question") -> Question:
    draft = QuestionDraft(
        source_name="test",
        source_item_id="q1",
        variant=1,
        subject="高数",
        question_type="计算题",
        question_text=text,
        normalized_fingerprint="0" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    q = Question(
        draft_id=draft.id,
        question_text=text,
        verification_status="verified",
        review_status="approved",
        knowledge_match_status="current",
    )
    session.add(q)
    session.flush()
    return q


def _make_link(session, question_id, kn_id, relation_type="primary") -> QuestionKnowledgeLink:
    link = QuestionKnowledgeLink(
        question_id=question_id,
        knowledge_node_id=kn_id,
        relation_type=relation_type,
        confidence=1.0,
    )
    session.add(link)
    session.flush()
    return link


def _valid_curriculum_ids(session) -> set[str]:
    return {cn.id for cn in session.scalars(select(CurriculumNode)).all()}


def _linked_orphaned_count(session) -> int:
    valid = _valid_curriculum_ids(session)
    return session.scalar(
        select(func.count(func.distinct(KnowledgeNode.id))).where(
            KnowledgeNode.curriculum_node_id.is_not(None),
            ~KnowledgeNode.curriculum_node_id.in_(valid) if valid else func.true(),
            KnowledgeNode.id.in_(
                select(QuestionKnowledgeLink.knowledge_node_id)
            ),
        )
    ) or 0


def _duplicate_link_count(session) -> int:
    subq = (
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
    return session.scalar(
        select(func.count()).select_from(subq)
    ) or 0


# ── Setup fixture ───────────────────────────────────────────────

@pytest.fixture
def orphaned_setup(session):
    """Create: old curriculum (deleted), old textbook_directory KN, new curriculum + new directory KN, question linked to old KN."""
    book = _make_textbook(session)

    # Old curriculum node (will be deleted to simulate orphan)
    old_cn = _make_curriculum(session, book.id, "1.3-old", "函数的极限")
    old_kn = _make_textbook_directory_kn(session, "函数的极限", old_cn.id)

    # New curriculum node + new directory KN
    new_cn = _make_curriculum(session, book.id, "1.3", "函数的极限")
    new_kn = _make_directory_kn(session, "函数的极限", new_cn.id)

    # Delete old curriculum node to create orphan
    session.delete(old_cn)
    session.flush()

    # Create question linked to old orphaned KN
    q = _make_question(session, "求极限")
    _make_link(session, q.id, old_kn.id, "primary")

    return {
        "book": book,
        "old_kn": old_kn,
        "new_kn": new_kn,
        "new_cn": new_cn,
        "question": q,
    }


# ── Test 1: Preview does not write to database ──────────────────

def test_preview_does_not_write(session, orphaned_setup):
    old_kn = orphaned_setup["old_kn"]

    link_before = session.scalar(
        select(func.count()).select_from(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.knowledge_node_id == old_kn.id
        )
    )
    assert link_before == 1

    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)

    # No changes after preview
    link_after = session.scalar(
        select(func.count()).select_from(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.knowledge_node_id == old_kn.id
        )
    )
    assert link_after == 1
    assert preview.safe_mappings == 1
    assert preview.unmatched_mappings == 0
    assert preview.ambiguous_mappings == 0


# ── Test 2: Unique name match → safe ────────────────────────────

def test_unique_name_match_safe(session, orphaned_setup):
    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)

    assert len(preview.mappings) == 1
    m = preview.mappings[0]
    assert m.status == "safe"
    assert m.new_kn_id == orphaned_setup["new_kn"].id
    assert m.old_kn_name == "函数的极限"
    assert m.new_kn_name == "函数的极限"


# ── Test 3: Zero matches → needs_manual_review ──────────────────

def test_zero_match_needs_review(session, orphaned_setup):
    # Remove the new KN so there's no match
    session.delete(orphaned_setup["new_kn"])
    session.flush()

    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)

    m = preview.mappings[0]
    assert m.status == "needs_manual_review"
    assert "no name match" in m.reason
    assert preview.unmatched_mappings == 1
    assert preview.safe_mappings == 0


# ── Test 4: Multiple matches → needs_manual_review ──────────────

def test_multi_match_needs_review(session, orphaned_setup):
    # Create a second directory KN with the same name but different curriculum
    book2 = _make_textbook(session, "高数下册")
    cn2 = _make_curriculum(session, book2.id, "2.3", "函数的极限")
    _make_directory_kn(session, "函数的极限", cn2.id)

    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)

    m = preview.mappings[0]
    assert m.status == "needs_manual_review"
    assert "multiple matches" in m.reason
    assert preview.ambiguous_mappings == 1
    assert preview.safe_mappings == 0


# ── Test 5: Link migration (simple case) ────────────────────────

def test_link_migration(session, orphaned_setup):
    old_kn = orphaned_setup["old_kn"]
    new_kn = orphaned_setup["new_kn"]
    question = orphaned_setup["question"]

    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)

    result = execute_migration(session, preview)

    assert result["updated"] == 1
    assert result["deleted"] == 0
    assert result["linked_orphaned_kn"] == 0

    # Old link should now point to new KN
    link = session.scalar(
        select(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.question_id == question.id
        )
    )
    assert link is not None
    assert link.knowledge_node_id == new_kn.id

    # No links to old KN
    old_links = session.scalar(
        select(func.count()).select_from(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.knowledge_node_id == old_kn.id
        )
    )
    assert old_links == 0


# ── Test 6: Mixed-link dedup ────────────────────────────────────

def test_mixed_link_dedup(session, orphaned_setup):
    new_kn = orphaned_setup["new_kn"]
    question = orphaned_setup["question"]

    # Add a link to the new KN (simulates mixed association)
    _make_link(session, question.id, new_kn.id, "primary")

    # Now the question has both old_kn(primary) and new_kn(primary) — same relation_type
    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)
    assert preview.mixed_questions == 1

    result = execute_migration(session, preview)

    # Old link should be deleted, new link preserved
    assert result["deleted"] == 1
    assert result["updated"] == 0

    # Only one link remains, pointing to new KN
    links = list(session.scalars(
        select(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.question_id == question.id
        )
    ).all())
    assert len(links) == 1
    assert links[0].knowledge_node_id == new_kn.id

    assert _duplicate_link_count(session) == 0


# ── Test 7: Transaction rollback ────────────────────────────────

def test_transaction_rollback(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'rollback.db'}"
    create_schema(database_url)
    factory = build_session_factory(database_url)

    # Seed data
    with factory.begin() as sa:
        book = Textbook(name="test", is_active=True)
        sa.add(book)
        sa.flush()
        old_cn = CurriculumNode(textbook_id=book.id, node_type="section", code="1.1", title="极限", sort_order=0)
        sa.add(old_cn)
        sa.flush()
        old_kn = KnowledgeNode(
            curriculum_node_id=old_cn.id, node_type="concept", name="极限",
            normalized_name="极限", source_type="textbook_directory", confidence=1.0, review_status="approved",
        )
        sa.add(old_kn)
        sa.flush()
        new_cn = CurriculumNode(textbook_id=book.id, node_type="section", code="1.1", title="极限", sort_order=1)
        sa.add(new_cn)
        sa.flush()
        new_kn = KnowledgeNode(
            curriculum_node_id=new_cn.id, node_type="concept", name="极限",
            normalized_name=f"极限::{new_cn.id}", source_type="directory", confidence=1.0, review_status="approved",
        )
        sa.add(new_kn)
        sa.flush()
        sa.delete(old_cn)
        sa.flush()
        draft = QuestionDraft(
            source_name="t", source_item_id="q1", variant=1, subject="高数",
            question_type="计算题", question_text="test", normalized_fingerprint="0"*64, status="approved",
        )
        sa.add(draft)
        sa.flush()
        q = Question(
            draft_id=draft.id, question_text="test", verification_status="verified",
            review_status="approved", knowledge_match_status="current",
        )
        sa.add(q)
        sa.flush()
        sa.add(QuestionKnowledgeLink(
            question_id=q.id, knowledge_node_id=old_kn.id, relation_type="primary", confidence=1.0,
        ))

    # Simulate failure during execute_migration: partial work then raise.
    # The exception must propagate OUT of the factory.begin() block so the
    # context manager rolls back the transaction (never commits partial work).
    def _failing_execute(sess, preview):
        for m in preview.mappings:
            if m.status == "safe":
                links = list(sess.scalars(
                    select(QuestionKnowledgeLink).where(
                        QuestionKnowledgeLink.knowledge_node_id == m.old_kn_id
                    )
                ).all())
                for link in links:
                    link.knowledge_node_id = m.new_kn_id
                    break  # Only migrate one, then fail
        raise RuntimeError("simulated failure")

    # Attempt migration — the RuntimeError escapes the with-block → rollback
    with pytest.raises(RuntimeError):
        with factory.begin() as sb:
            valid = {cn.id for cn in sb.scalars(select(CurriculumNode)).all()}
            preview = build_mapping(sb, valid)
            _failing_execute(sb, preview)

    # Verify data is unchanged
    with factory.begin() as sc:
        link = sc.scalar(
            select(QuestionKnowledgeLink).where(
                QuestionKnowledgeLink.knowledge_node_id == old_kn.id
            )
        )
        assert link is not None  # Old link still exists


# ── Test 8: knowledge_match_status stays current ────────────────

def test_kms_stays_current(session, orphaned_setup):
    question = orphaned_setup["question"]
    assert question.knowledge_match_status == "current"

    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)
    execute_migration(session, preview)

    # Refresh
    session.refresh(question)
    assert question.knowledge_match_status == "current"


# ── Test 9: Post-condition validation ───────────────────────────

def test_post_condition(session, orphaned_setup):
    valid = _valid_curriculum_ids(session)
    preview = build_mapping(session, valid)
    result = execute_migration(session, preview)

    assert result["linked_orphaned_kn"] == 0
    assert result["duplicate_links"] == 0
    assert _linked_orphaned_count(session) == 0
    assert _duplicate_link_count(session) == 0


# ── Test 10: Taxonomy replace regression ────────────────────────

def test_taxonomy_replace_no_orphaned_kn(session):
    """After a taxonomy replace, no QuestionKnowledgeLink should point to an orphaned KN.

    Production ordering: legacy textbook_directory KN exists BEFORE the replace
    import runs. The root-cause fix (source_type.in_(['directory','textbook_directory']))
    feeds legacy KNs into sync_directory_knowledge_nodes, which reuses them by name —
    updating curriculum_node_id to the new node instead of leaving it dangling.
    """
    from calculus_agent.api import import_textbook_directory

    book = _make_textbook(session)

    # Step 1: legacy state — old curriculum + textbook_directory KN + question link.
    # Created BEFORE any import so the import's collision-aware normalized_name
    # logic sees the legacy KN and suffixes any new directory KN sharing the name.
    old_cn = _make_curriculum(session, book.id, "3.1", "微分中值定理")
    legacy_kn = _make_textbook_directory_kn(session, "罗尔定理", old_cn.id)
    q = _make_question(session, "证明罗尔定理")
    _make_link(session, q.id, legacy_kn.id, "primary")

    # Step 2: taxonomy replace — new directory text replaces old curriculum.
    new_text = (
        "第三章 微分中值定理与导数的应用\n"
        "3.1 微分中值定理\n"
        "罗尔定理\n"
    )
    import_textbook_directory(book.id, {"text": new_text, "replace": True, "strict": True}, session)

    # Post-condition: no linked orphaned KN
    assert _linked_orphaned_count(session) == 0

    # The question's link should still exist and point to a valid KN
    link = session.scalar(
        select(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.question_id == q.id
        )
    )
    assert link is not None
    linked_kn = session.get(KnowledgeNode, link.knowledge_node_id)
    assert linked_kn is not None
    assert linked_kn.curriculum_node_id is not None
    # The linked KN should be in the valid curriculum
    valid = _valid_curriculum_ids(session)
    assert linked_kn.curriculum_node_id in valid
