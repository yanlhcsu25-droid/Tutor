"""Deterministic question -> chapter ownership helpers.

Business contract:
- Current knowledge links + curriculum taxonomy determine chapter ownership.
- Cross-chapter questions belong to the latest chapter in textbook order.
- Question.curriculum_chapter_id is the materialized ownership used by filters
  and generation; every knowledge write must synchronize it transactionally.
- Broken taxonomy, cross-textbook knowledge, or otherwise unresolvable
  knowledge produces no owning chapter rather than a guessed chapter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.chapter import (
    ChapterResolution,
    resolve_chapter_from_knowledge_ids,
)
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionKnowledgeLink,
    Textbook,
)


_CHAPTER_REF = re.compile(r"^第([一二三四五六七八九十百0-9]+)章(?:\s+.*)?$")


def _active_textbook_id(session: Session) -> str | None:
    return session.scalar(
        select(Textbook.id)
        .where(Textbook.is_active.is_(True))
        .order_by(Textbook.created_at.desc(), Textbook.id)
        .limit(1)
    )


def list_active_chapters(session: Session) -> list[CurriculumNode]:
    statement = select(CurriculumNode).where(
        CurriculumNode.node_type == "chapter",
        CurriculumNode.review_status == "approved",
    )
    textbook_id = _active_textbook_id(session)
    if textbook_id is not None:
        statement = statement.where(CurriculumNode.textbook_id == textbook_id)
    return list(
        session.scalars(
            statement.order_by(CurriculumNode.sort_order, CurriculumNode.id)
        ).all()
    )


def _chinese_number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    digits = {
        "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9,
    }
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if value.startswith("十") and len(value) == 2 and value[1] in digits:
        return 10 + digits[value[1]]
    if value.endswith("十") and len(value) == 2 and value[0] in digits:
        return digits[value[0]] * 10
    if "十" in value:
        left, right = value.split("十", 1)
        if left in digits and right in digits:
            return digits[left] * 10 + digits[right]
    if value == "百":
        return 100
    return None


def _chapter_ordinal_from_text(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip()
    match = _CHAPTER_REF.match(text)
    if match:
        return _chinese_number(match.group(1))
    return _chinese_number(text)


def chapter_display_name(chapter: CurriculumNode | None) -> str | None:
    if chapter is None:
        return None
    title = (chapter.title or "").strip()
    if _CHAPTER_REF.match(title):
        return title
    code = (chapter.code or "").strip()
    if _CHAPTER_REF.match(code):
        prefix = code
    else:
        ordinal = _chapter_ordinal_from_text(code)
        prefix = f"第{code}章" if ordinal is not None and code else ""
    if prefix and title:
        return f"{prefix} {title}"
    return title or prefix or None


def resolve_chapter_reference(
    session: Session,
    *,
    chapter_id: str | None = None,
    label: str | None = None,
) -> CurriculumNode | None:
    chapters = list_active_chapters(session)
    by_id = {chapter.id: chapter for chapter in chapters}
    if chapter_id:
        return by_id.get(chapter_id)
    if not label or not label.strip():
        return None

    normalized = normalize_name(label)
    exact = [
        chapter
        for chapter in chapters
        if normalized
        in {
            normalize_name(chapter.title or ""),
            normalize_name(chapter.code or ""),
            normalize_name(chapter_display_name(chapter) or ""),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    requested_ordinal = _chapter_ordinal_from_text(label)
    if requested_ordinal is not None:
        ordinal_matches = [
            chapter
            for chapter in chapters
            if _chapter_ordinal_from_text(chapter.code) == requested_ordinal
            or _chapter_ordinal_from_text(chapter.title) == requested_ordinal
        ]
        if len(ordinal_matches) == 1:
            return ordinal_matches[0]
        if len(ordinal_matches) > 1:
            return None
        if 1 <= requested_ordinal <= len(chapters):
            return chapters[requested_ordinal - 1]
    return None


def _owning_chapter_for_curriculum_node(
    session: Session, curriculum_node_id: str | None
) -> CurriculumNode | None:
    seen: set[str] = set()
    current_id = curriculum_node_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        current = session.get(CurriculumNode, current_id)
        if current is None:
            return None
        if current.node_type == "chapter":
            return current
        current_id = current.parent_id
    return None


def chapters_for_knowledge_ids(
    session: Session, knowledge_ids: Iterable[str]
) -> list[CurriculumNode]:
    chapters: dict[str, CurriculumNode] = {}
    for node_id in dict.fromkeys(item for item in knowledge_ids if item):
        node = session.get(KnowledgeNode, node_id)
        if node is None:
            continue
        chapter = _owning_chapter_for_curriculum_node(
            session, node.curriculum_node_id
        )
        if chapter is not None:
            chapters[chapter.id] = chapter
    order = {
        chapter.id: index
        for index, chapter in enumerate(list_active_chapters(session))
    }
    return sorted(
        chapters.values(),
        key=lambda item: (order.get(item.id, 10**9), item.sort_order, item.id),
    )


def derive_default_chapter_from_knowledge(
    session: Session, knowledge_ids: Iterable[str]
) -> CurriculumNode | None:
    """Return the deterministic owner chapter for explicit semantic knowledge."""
    resolution = resolve_chapter_from_knowledge_ids(
        session,
        list(dict.fromkeys(item for item in knowledge_ids if item)),
    )
    if resolution.status != "ok" or resolution.chapter_id is None:
        return None
    return session.get(CurriculumNode, resolution.chapter_id)


def sync_question_chapter_ownership(
    session: Session,
    question_id: str,
) -> ChapterResolution:
    """Synchronize materialized ownership from current semantic knowledge links."""
    question = session.get(Question, question_id)
    if question is None:
        raise LookupError(f"Question not found: {question_id}")

    knowledge_ids = list(session.scalars(
        select(QuestionKnowledgeLink.knowledge_node_id)
        .join(
            KnowledgeNode,
            KnowledgeNode.id == QuestionKnowledgeLink.knowledge_node_id,
        )
        .where(
            QuestionKnowledgeLink.question_id == question_id,
            KnowledgeNode.node_type.in_(("concept", "knowledge_point")),
        )
    ).all())

    resolution = resolve_chapter_from_knowledge_ids(session, knowledge_ids)
    question.curriculum_chapter_id = (
        resolution.chapter_id if resolution.status == "ok" else None
    )
    session.flush()
    return resolution


def reconcile_question_chapter_assignments(session: Session) -> int:
    """Recompute materialized ownership for all questions, idempotently."""
    updated = 0
    questions = list(session.scalars(select(Question)).all())
    for question in questions:
        before = question.curriculum_chapter_id
        sync_question_chapter_ownership(session, question.id)
        if question.curriculum_chapter_id != before:
            updated += 1
    session.flush()
    return updated


def question_chapter_display(session: Session, question: Question) -> str | None:
    if not question.curriculum_chapter_id:
        return None
    return chapter_display_name(
        session.get(CurriculumNode, question.curriculum_chapter_id)
    )


def scope_labels_are_whole_chapters(
    session: Session, labels: Iterable[str]
) -> bool:
    values = [label for label in labels if label]
    return bool(values) and all(
        resolve_chapter_reference(session, label=label) is not None
        for label in values
    )


def resolve_scope_chapter_ids(
    session: Session,
    labels: Iterable[str],
    scope_knowledge_node_ids: Iterable[str] = (),
) -> list[str]:
    resolved: dict[str, CurriculumNode] = {}
    unresolved_label = False
    for label in labels:
        chapter = resolve_chapter_reference(session, label=label)
        if chapter is None:
            unresolved_label = True
            continue
        resolved[chapter.id] = chapter

    if unresolved_label or not resolved:
        for chapter in chapters_for_knowledge_ids(
            session, scope_knowledge_node_ids
        ):
            resolved[chapter.id] = chapter

    order = {
        chapter.id: index
        for index, chapter in enumerate(list_active_chapters(session))
    }
    return sorted(resolved, key=lambda item: (order.get(item, 10**9), item))


def backfill_question_chapter_assignments(session: Session) -> int:
    """Compatibility helper: fill currently-unowned questions from taxonomy."""
    updated = 0
    questions = list(session.scalars(
        select(Question).where(Question.curriculum_chapter_id.is_(None))
    ).all())
    for question in questions:
        before = question.curriculum_chapter_id
        sync_question_chapter_ownership(session, question.id)
        if before != question.curriculum_chapter_id:
            updated += 1
    session.flush()
    return updated
