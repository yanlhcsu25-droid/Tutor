"""Validation boundary between curriculum candidate recall and teaching scope."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.application.curriculum_retrieval import CurriculumCandidate
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook
from calculus_agent.questions.chapter_assignment import chapter_display_name


ScopeLevel = Literal["chapter", "section", "knowledge", "mixed"]


class TeachingScopeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_node_ids: list[str] = Field(default_factory=list, max_length=20)
    reasoning: str = Field(default="", max_length=2000)


class ValidatedTeachingScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: TeachingScopeDecision
    selected_nodes: list[dict]
    inferred_scope_level: ScopeLevel
    validated_scope_names: list[str]
    selected_knowledge_names: list[str] = Field(default_factory=list)


class CurriculumScopeCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_matches: list[CurriculumCandidate]
    selectable_scopes: list[CurriculumCandidate]


class TeachingScopeValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def project_selectable_teaching_scopes(
    session: Session,
    *,
    semantic_matches: list[CurriculumCandidate],
) -> CurriculumScopeCandidates:
    """Project semantic hits onto DB-backed nodes legal for scope selection."""
    if not semantic_matches:
        return CurriculumScopeCandidates(
            semantic_matches=[],
            selectable_scopes=[],
        )

    active_textbook_ids = set(session.scalars(
        select(Textbook.id).where(Textbook.is_active.is_(True))
    ).all())
    curriculum = list(session.scalars(select(CurriculumNode).where(
        CurriculumNode.review_status == "approved",
        CurriculumNode.textbook_id.in_(active_textbook_ids),
    )).all()) if active_textbook_ids else []
    curriculum_by_id = {node.id: node for node in curriculum}
    match_ids = [item.node_id for item in semantic_matches]
    knowledge_by_id = {
        node.id: node
        for node in session.scalars(select(KnowledgeNode).where(
            KnowledgeNode.id.in_(match_ids),
            KnowledgeNode.review_status == "approved",
        )).all()
    }

    def path_for(node_id: str | None) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        current = node_id
        while current and current not in seen:
            seen.add(current)
            node = curriculum_by_id.get(current)
            if node is None:
                break
            result.append(node.title)
            current = node.parent_id
        return list(reversed(result))

    selectable_by_id: dict[str, CurriculumCandidate] = {}

    def add_curriculum(node: CurriculumNode, score: float) -> None:
        if node.node_type not in {"chapter", "section"}:
            return
        candidate = CurriculumCandidate(
            node_id=node.id,
            node_type=node.node_type,
            title=node.title,
            parent_path=path_for(node.parent_id),
            similarity_score=score,
        )
        previous = selectable_by_id.get(node.id)
        if previous is None or candidate.similarity_score > previous.similarity_score:
            selectable_by_id[node.id] = candidate

    def add_ancestors(node_id: str | None, score: float) -> None:
        seen: set[str] = set()
        current = node_id
        while current and current not in seen:
            seen.add(current)
            node = curriculum_by_id.get(current)
            if node is None:
                break
            add_curriculum(node, score)
            current = node.parent_id

    for match in semantic_matches:
        knowledge = knowledge_by_id.get(match.node_id)
        if knowledge is not None:
            if knowledge.node_type == "knowledge_point":
                selectable_by_id[knowledge.id] = CurriculumCandidate(
                    node_id=knowledge.id,
                    node_type=knowledge.node_type,
                    title=knowledge.name,
                    parent_path=path_for(knowledge.curriculum_node_id),
                    similarity_score=match.similarity_score,
                )
            add_ancestors(
                knowledge.curriculum_node_id,
                match.similarity_score,
            )
            continue

        curriculum_node = curriculum_by_id.get(match.node_id)
        if curriculum_node is not None:
            add_curriculum(curriculum_node, match.similarity_score)
            add_ancestors(
                curriculum_node.parent_id,
                match.similarity_score,
            )

    type_priority = {"section": 3, "chapter": 2, "knowledge_point": 1}
    selectable = sorted(
        selectable_by_id.values(),
        key=lambda item: (
            -item.similarity_score,
            -type_priority.get(item.node_type, 0),
            len(item.parent_path),
            item.title,
            item.node_id,
        ),
    )[:20]
    return CurriculumScopeCandidates(
        semantic_matches=semantic_matches,
        selectable_scopes=selectable,
    )


def validate_teaching_scope_decision(
    session: Session,
    *,
    decision: TeachingScopeDecision,
    candidate_node_ids: set[str],
) -> ValidatedTeachingScope:
    """Validate an LLM decision against the exact retrieval snapshot."""
    selected_ids = list(dict.fromkeys(decision.selected_node_ids))
    if not selected_ids:
        raise TeachingScopeValidationError(
            "teaching_scope_selection_empty",
            "没有选择任何教材候选。",
        )
    if len(selected_ids) != len(decision.selected_node_ids):
        raise TeachingScopeValidationError(
            "teaching_scope_duplicate_nodes",
            "教学范围不能重复选择同一节点。",
        )
    if not set(selected_ids).issubset(candidate_node_ids):
        raise TeachingScopeValidationError(
            "teaching_scope_not_in_candidates",
            "教学范围只能从本轮教材召回候选中选择。",
        )

    active_textbook_ids = set(session.scalars(
        select(Textbook.id).where(Textbook.is_active.is_(True))
    ).all())
    if not active_textbook_ids:
        raise TeachingScopeValidationError(
            "active_textbook_required",
            "当前没有激活教材，无法确认教学范围。",
        )

    curriculum = list(session.scalars(select(CurriculumNode).where(
        CurriculumNode.review_status == "approved",
    )).all())
    curriculum_by_id = {node.id: node for node in curriculum}
    knowledge = list(session.scalars(select(KnowledgeNode).where(
        KnowledgeNode.id.in_(selected_ids),
        KnowledgeNode.review_status == "approved",
    )).all())
    knowledge_by_id = {node.id: node for node in knowledge}

    def owning_chapter(node_id: str | None) -> CurriculumNode:
        current = node_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            node = curriculum_by_id.get(current)
            if node is None:
                break
            if node.textbook_id not in active_textbook_ids:
                raise TeachingScopeValidationError(
                    "teaching_scope_wrong_textbook",
                    "候选节点不属于当前激活教材。",
                )
            if node.node_type == "chapter":
                return node
            current = node.parent_id
        raise TeachingScopeValidationError(
            "teaching_scope_invalid_hierarchy",
            "候选节点无法沿教材层级追溯到章节。",
        )

    selected_nodes: list[dict] = []
    chapters: list[CurriculumNode] = []
    knowledge_names: list[str] = []
    inferred_levels: set[ScopeLevel] = set()
    for node_id in selected_ids:
        curriculum_node = curriculum_by_id.get(node_id)
        knowledge_node = knowledge_by_id.get(node_id)
        if curriculum_node is None and knowledge_node is None:
            raise TeachingScopeValidationError(
                "teaching_scope_node_not_found",
                "所选教材节点不存在或尚未审核。",
            )

        if knowledge_node is not None:
            chapter = owning_chapter(knowledge_node.curriculum_node_id)
            inferred_levels.add("knowledge")
            selected_nodes.append({
                "node_id": knowledge_node.id,
                "node_type": knowledge_node.node_type,
                "title": knowledge_node.name,
            })
            knowledge_names.append(knowledge_node.name)
        else:
            if curriculum_node.node_type not in {"chapter", "section"}:
                raise TeachingScopeValidationError(
                    "teaching_scope_unsupported_node_type",
                    "所选目录项不能直接作为教学范围。",
                )
            chapter = owning_chapter(curriculum_node.id)
            inferred_levels.add(curriculum_node.node_type)
            selected_nodes.append({
                "node_id": curriculum_node.id,
                "node_type": curriculum_node.node_type,
                "title": curriculum_node.title,
            })
        chapters.append(chapter)

    unique_chapters = {chapter.id: chapter for chapter in chapters}
    scope_names = [
        chapter_display_name(chapter) or chapter.title
        for chapter in sorted(
            unique_chapters.values(),
            key=lambda item: (item.sort_order, item.id),
        )
    ]
    inferred_scope_level: ScopeLevel = (
        next(iter(inferred_levels))
        if len(inferred_levels) == 1
        else "mixed"
    )
    return ValidatedTeachingScope(
        decision=decision,
        selected_nodes=selected_nodes,
        inferred_scope_level=inferred_scope_level,
        validated_scope_names=scope_names,
        selected_knowledge_names=knowledge_names,
    )
