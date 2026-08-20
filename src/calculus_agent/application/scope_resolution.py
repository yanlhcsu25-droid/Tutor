"""Single boundary for teacher-facing curriculum scope labels."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import CurriculumNode, KnowledgeNode
from calculus_agent.questions.chapter_assignment import (
    chapter_display_name,
    resolve_chapter_reference,
)


class ResolvedScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_labels: list[str]
    validated_scope_names: list[str] = Field(default_factory=list)
    unresolved_labels: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.validated_scope_names) and not self.unresolved_labels


def resolve_deterministic_scope_labels(
    session: Session,
    labels: list[str],
) -> ResolvedScope:
    """Resolve explicit chapter/section/knowledge labels without an LLM."""
    requested = list(dict.fromkeys(
        value.strip() for value in labels if value and value.strip()
    ))
    if not requested:
        return ResolvedScope(requested_labels=[])

    curriculum = list(session.scalars(select(CurriculumNode).where(
        CurriculumNode.review_status == "approved",
    )).all())
    knowledge = list(session.scalars(select(KnowledgeNode).where(
        KnowledgeNode.review_status == "approved",
    )).all())
    curriculum_by_id = {item.id: item for item in curriculum}

    def owner_chapter(node_id: str | None) -> CurriculumNode | None:
        seen: set[str] = set()
        current = node_id
        while current and current not in seen:
            seen.add(current)
            node = curriculum_by_id.get(current)
            if node is None:
                return None
            if node.node_type == "chapter":
                return node
            current = node.parent_id
        return None

    resolved: list[str] = []
    unresolved: list[str] = []
    for label in requested:
        chapter = resolve_chapter_reference(session, label=label)
        if chapter is None:
            catalog_alias = re.sub(
                r"^(?:高数|高等数学)(?:上册?|下册?)?",
                "",
                label,
            ).strip()
            if catalog_alias and catalog_alias != label:
                chapter = resolve_chapter_reference(
                    session,
                    label=catalog_alias,
                )
        if chapter is not None:
            resolved.append(chapter_display_name(chapter) or chapter.title)
            continue

        normalized = normalize_name(label)
        curriculum_matches = [
            node for node in curriculum
            if normalize_name(node.title) == normalized
        ]
        knowledge_matches = [
            node for node in knowledge
            if normalize_name(node.name) == normalized
            or node.normalized_name == normalized
        ]
        if len(curriculum_matches) == 1:
            chapter = owner_chapter(curriculum_matches[0].id)
            if chapter is not None:
                resolved.append(chapter_display_name(chapter) or chapter.title)
                continue
        if len(knowledge_matches) == 1:
            chapter = owner_chapter(knowledge_matches[0].curriculum_node_id)
            if chapter is not None:
                resolved.append(chapter_display_name(chapter) or chapter.title)
                continue
        unresolved.append(label)

    return ResolvedScope(
        requested_labels=requested,
        validated_scope_names=list(dict.fromkeys(resolved)),
        unresolved_labels=unresolved,
    )
