"""Deterministic question-chapter derivation from the knowledge taxonomy.

Single, authoritative source of truth:

    Question
      -> QuestionKnowledgeLink
      -> KnowledgeNode
      -> CurriculumNode taxonomy
      -> Chapter

Rule: a question's chapter is the **latest** chapter (by textbook catalog
order, i.e. ``CurriculumNode.sort_order``) among all chapters that the
question's associated knowledge points belong to. Cross-chapter is NOT treated
as an error; we simply pick the most recent chapter.

Design notes (project invariant):
* We never parse "第一章" / "第二章" strings for ordering.
* We never use lexicographic string comparison.
* We reuse the existing deterministic catalog order (``CurriculumNode.sort_order``),
  the same order already used by ``list_top_level_chapters`` and
  ``chapter_descendant_knowledge_ids``.
* No LLM, no free-text, and ``QuestionDraft.source_topic`` are ever consulted
  for the formal chapter. ``source_topic`` remains OCR/dataset provenance only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import CurriculumNode, KnowledgeNode, QuestionKnowledgeLink

ChapterStatus = Literal["ok", "missing", "unresolvable"]
_CHAPTER_KNOWLEDGE_NODE_TYPES = ("concept", "knowledge_point")


@dataclass(frozen=True)
class ChapterResolution:
    status: ChapterStatus
    chapter_id: str | None
    chapter_name: str | None


def _walk_to_chapter(
    node_id: str, node_by_id: dict[str, CurriculumNode]
) -> CurriculumNode | None:
    """Walk a curriculum node upward until a node with node_type=='chapter'.

    Returns the chapter node, or None if the chain is broken / has no chapter
    ancestor. A ``seen`` guard prevents infinite loops on malformed trees.
    """
    seen: set[str] = set()
    cur_id: str | None = node_id
    while cur_id is not None and cur_id not in seen:
        seen.add(cur_id)
        node = node_by_id.get(cur_id)
        if node is None:
            return None
        if node.node_type == "chapter":
            return node
        cur_id = node.parent_id
    return None


def _resolve_given_maps(
    knowledge_ids: list[str],
    kn_by_id: dict[str, KnowledgeNode],
    node_by_id: dict[str, CurriculumNode],
) -> ChapterResolution:
    if not knowledge_ids:
        return ChapterResolution(status="missing", chapter_id=None, chapter_name=None)

    chapters: list[CurriculumNode] = []
    any_unresolved = False
    for kn_id in knowledge_ids:
        kn = kn_by_id.get(kn_id)
        if kn is None or not kn.curriculum_node_id:
            any_unresolved = True
            continue
        chapter = _walk_to_chapter(kn.curriculum_node_id, node_by_id)
        if chapter is None:
            any_unresolved = True
            continue
        chapters.append(chapter)

    # A knowledge point that cannot be traced to a chapter (missing node,
    # broken taxonomy, or no chapter ancestor) makes the chapter undecidable.
    if any_unresolved or not chapters:
        return ChapterResolution(status="unresolvable", chapter_id=None, chapter_name=None)

    # Chapters from incomparable textbooks cannot be ordered -> undecidable.
    textbooks = {c.textbook_id for c in chapters if c.textbook_id}
    if len(textbooks) > 1:
        return ChapterResolution(status="unresolvable", chapter_id=None, chapter_name=None)

    # Latest chapter by textbook catalog order; id is a deterministic tiebreak.
    latest = max(chapters, key=lambda c: (c.sort_order, c.id))
    return ChapterResolution(
        status="ok", chapter_id=latest.id, chapter_name=latest.title
    )


def _load_node_maps(
    session: Session, knowledge_ids: list[str]
) -> tuple[dict[str, KnowledgeNode], dict[str, CurriculumNode]]:
    """Load KnowledgeNodes and their CurriculumNode ancestors (BFS)."""
    kn_by_id: dict[str, KnowledgeNode] = {}
    cn_ids: set[str] = set()
    if knowledge_ids:
        for kn in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.id.in_(knowledge_ids))
        ).all():
            kn_by_id[kn.id] = kn
            if kn.curriculum_node_id:
                cn_ids.add(kn.curriculum_node_id)

    node_by_id: dict[str, CurriculumNode] = {}
    frontier = set(cn_ids)
    while frontier:
        nodes = list(session.scalars(
            select(CurriculumNode).where(CurriculumNode.id.in_(frontier))
        ).all())
        frontier = set()
        for node in nodes:
            if node.id in node_by_id:
                continue
            node_by_id[node.id] = node
            if node.parent_id and node.parent_id not in node_by_id:
                frontier.add(node.parent_id)
    return kn_by_id, node_by_id


def resolve_chapter_from_knowledge_ids(
    session: Session, knowledge_ids: list[str]
) -> ChapterResolution:
    """Resolve chapter for an explicit list of knowledge-node ids.

    Used by OCR/import provenance and by tests. Single question/link paths
    delegate here so there is exactly ONE chapter-finding rule in the project.
    """
    knowledge_ids = list(dict.fromkeys(knowledge_ids))
    kn_by_id, node_by_id = _load_node_maps(session, knowledge_ids)
    return _resolve_given_maps(knowledge_ids, kn_by_id, node_by_id)


def resolve_question_chapter(session: Session, question_id: str) -> ChapterResolution:
    """Resolve the chapter for one question from its current knowledge links."""
    link_ids = list(session.scalars(
        select(QuestionKnowledgeLink.knowledge_node_id)
        .join(
            KnowledgeNode,
            KnowledgeNode.id == QuestionKnowledgeLink.knowledge_node_id,
        )
        .where(
            QuestionKnowledgeLink.question_id == question_id,
            KnowledgeNode.node_type.in_(_CHAPTER_KNOWLEDGE_NODE_TYPES),
        )
    ).all())
    return resolve_chapter_from_knowledge_ids(session, link_ids)


def resolve_questions_chapters(
    session: Session, question_ids: list[str]
) -> dict[str, ChapterResolution]:
    """Batch resolver for many questions — avoids N+1 on list endpoints.

    Loads every required ``QuestionKnowledgeLink``, ``KnowledgeNode`` and
    ``CurriculumNode`` (plus ancestors) in a handful of queries, then merges in
    Python. Suitable for endpoints returning hundreds/thousands of rows.
    """
    question_ids = list(dict.fromkeys(question_ids))
    if not question_ids:
        return {}

    links = list(session.execute(
        select(
            QuestionKnowledgeLink.question_id,
            QuestionKnowledgeLink.knowledge_node_id,
        )
        .join(
            KnowledgeNode,
            KnowledgeNode.id == QuestionKnowledgeLink.knowledge_node_id,
        )
        .where(
            QuestionKnowledgeLink.question_id.in_(question_ids),
            KnowledgeNode.node_type.in_(_CHAPTER_KNOWLEDGE_NODE_TYPES),
        )
    ).all())

    by_question: dict[str, list[str]] = {}
    all_kn_ids: set[str] = set()
    for qid, kn_id in links:
        by_question.setdefault(qid, []).append(kn_id)
        all_kn_ids.add(kn_id)

    kn_by_id, node_by_id = _load_node_maps(session, list(all_kn_ids))

    return {
        qid: _resolve_given_maps(by_question.get(qid, []), kn_by_id, node_by_id)
        for qid in question_ids
    }
