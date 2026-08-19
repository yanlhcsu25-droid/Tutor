"""Reinforcement planning from wrong-question feedback on the current Paper.

V1 scope (deliberately narrow):

    wrong question  ->  reinforcement evidence  (NOT mastery diagnosis)

The service is the single deterministic boundary that turns a teacher's
structured feedback (resolved by the LLM from natural language) into a
:class:`~calculus_agent.agent.schemas.GenerationPlanPatch`. It never invents
database ids, never guesses knowledge points, never computes mastery. All
paper/question/knowledge facts come from the current Paper version.

The resulting patch is handed to the existing :class:`GenerationService`,
so the confirmation lifecycle stays exactly the one already used for normal
generation: ``prepare_reinforcement_plan -> waiting_confirmation -> confirm_generation``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.agent.schemas import (
    GenerationPlanPatch,
    GenerationPlanPreview,
)
from calculus_agent.knowledge.classification import (
    current_taxonomy_knowledge_nodes,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Paper,
    PaperItem,
    Question,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from calculus_agent.papers.addressing import (
    resolve_section_item_from_items,
    section_order_map,
)


class ReinforcementError(ValueError):
    """Deterministic failure with a machine-readable code for the Tool boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def reinforcement_weight(evidence_count: int) -> int:
    """V1 deterministic knowledge-priority weight.

    Pure, testable business rule. One wrong-question evidence -> 3,
    two -> 4, three or more -> 5. Never depends on the LLM.
    """
    return min(5, 2 + max(0, evidence_count))


class ReinforcementEvidence(BaseModel):
    paper_item_id: str
    question_id: str
    position: int
    section_type: str
    section_order: int
    question_type: str
    difficulty: int | None = None
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    teacher_note: str | None = None


class KnowledgeReinforcementTarget(BaseModel):
    knowledge_node_id: str
    knowledge_name: str
    evidence_count: int
    weight: int


class ReinforcementContext(BaseModel):
    source_paper_id: str
    source_question_ids: list[str] = Field(default_factory=list)
    evidence: list[ReinforcementEvidence] = Field(default_factory=list)
    target_knowledge: list[KnowledgeReinforcementTarget] = Field(
        default_factory=list
    )
    scope_names: list[str] = Field(default_factory=list)
    scope_chapter_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass
class ReinforcementResult:
    context: ReinforcementContext
    patch: GenerationPlanPatch
    preview: GenerationPlanPreview


class ReinforcementService:
    """Turn wrong-question feedback into a reinforcement generation patch.

    All-or-nothing: every feedback item is resolved and validated before any
    :class:`GenerationPlanPatch` is compiled or any pending generation is
    created. A single invalid reference aborts the whole preparation.
    """

    def __init__(self, session: Session, generation_service: Any):
        self.session = session
        self.generation_service = generation_service

    def prepare(
        self,
        paper_id: str,
        items: list[Any],
    ) -> ReinforcementResult:
        context = self.build_context(paper_id, items)
        patch = self.compile_patch(context)
        preview = self.generation_service.preview(
            patch,
            replace_existing_pending=True,
        )
        return ReinforcementResult(context=context, patch=patch, preview=preview)

    def build_context(self, paper_id: str, items: list[Any]) -> ReinforcementContext:
        paper = self.session.get(Paper, paper_id)
        if paper is None:
            raise ReinforcementError(
                "no_current_paper",
                "当前没有可操作的试卷版本，无法根据错题生成巩固卷。",
            )

        paper_items = list(
            self.session.scalars(
                select(PaperItem)
                .where(PaperItem.paper_id == paper_id)
                .order_by(PaperItem.position)
            )
        )
        if not paper_items:
            raise ReinforcementError(
                "no_current_paper",
                "当前试卷没有题目，无法根据错题生成巩固卷。",
            )

        section_orders = section_order_map(paper_items)
        items_by_position = {item.position: item for item in paper_items}
        valid_knowledge_ids = {
            node.id for node in current_taxonomy_knowledge_nodes(self.session)
        }
        nodes_by_id = {
            node.id: node for node in current_taxonomy_knowledge_nodes(self.session)
        }
        curriculum_by_id = {
            node.id: node for node in self.session.scalars(select(CurriculumNode))
        }

        resolved_items: list[PaperItem] = []
        seen_ids: set[str] = set()
        warnings: list[str] = []

        for feedback in items:
            item = self._resolve_item(feedback, paper_items, items_by_position)
            if item is None:
                raise ReinforcementError(
                    "feedback_question_not_found",
                    self._not_found_message(feedback),
                )
            if item.id in seen_ids:
                warnings.append("duplicate_feedback_reference_ignored")
                continue
            seen_ids.add(item.id)
            resolved_items.append(item)

        evidence: list[ReinforcementEvidence] = []
        source_question_ids: list[str] = []
        counts: Counter[str] = Counter()
        scope_chapter_ids: list[str] = []
        scope_names: list[str] = []

        for item in resolved_items:
            question = self.session.get(Question, item.question_id)
            if question is None:
                raise ReinforcementError(
                    "feedback_question_not_found",
                    "当前试卷题目缺少对应的题库题目记录，无法解析知识点。",
                )

            knowledge_ids = self._valid_knowledge_ids(
                question.id, valid_knowledge_ids
            )
            if not knowledge_ids:
                raise ReinforcementError(
                    "reinforcement_knowledge_unresolved",
                    "反馈的题目在当前知识点分类中没有可用知识点，"
                    "无法据此确定强化重点。",
                )

            chapter_id = question.curriculum_chapter_id
            chapter_title = self._chapter_title(chapter_id, curriculum_by_id)
            if chapter_title is None:
                raise ReinforcementError(
                    "reinforcement_scope_unresolved",
                    "反馈的题目无法确定其所属章节，无法据此确定巩固卷范围。",
                )

            if chapter_id not in scope_chapter_ids:
                scope_chapter_ids.append(chapter_id)
                scope_names.append(chapter_title)

            for knowledge_id in knowledge_ids:
                counts[knowledge_id] += 1

            if question.id not in source_question_ids:
                source_question_ids.append(question.id)

            evidence.append(ReinforcementEvidence(
                paper_item_id=item.id,
                question_id=question.id,
                position=item.position,
                section_type=item.section,
                section_order=section_orders.get(item.id, item.position),
                question_type=question.question_type,
                difficulty=self._difficulty(question.id),
                knowledge=[
                    {
                        "knowledge_node_id": kid,
                        "knowledge_name": nodes_by_id[kid].name,
                    }
                    for kid in knowledge_ids
                ],
                teacher_note=feedback.teacher_note,
            ))

        targets = [
            KnowledgeReinforcementTarget(
                knowledge_node_id=knowledge_id,
                knowledge_name=nodes_by_id[knowledge_id].name,
                evidence_count=evidence_count,
                weight=reinforcement_weight(evidence_count),
            )
            for knowledge_id, evidence_count in counts.items()
        ]
        # Stable, deterministic ordering: strongest evidence first, then name.
        targets.sort(key=lambda target: (-target.evidence_count, target.knowledge_name))

        context = ReinforcementContext(
            source_paper_id=paper_id,
            source_question_ids=source_question_ids,
            evidence=evidence,
            target_knowledge=targets,
            scope_names=scope_names,
            scope_chapter_ids=scope_chapter_ids,
            warnings=warnings,
        )
        return context

    def compile_patch(self, context: ReinforcementContext) -> GenerationPlanPatch:
        preferences = [target.knowledge_name for target in context.target_knowledge]
        weights = {
            target.knowledge_name: target.weight
            for target in context.target_knowledge
        }
        return GenerationPlanPatch(
            paper_type="chapter_exercise",
            scope_names=list(context.scope_names),
            knowledge_preferences=preferences,
            knowledge_priority_weights=weights,
        )

    def _resolve_item(self, feedback, paper_items, items_by_position) -> PaperItem | None:
        if feedback.address is not None:
            return resolve_section_item_from_items(
                paper_items,
                section_type=feedback.address.section_type,
                section_order=feedback.address.section_order,
            )
        return items_by_position.get(feedback.position)

    def _valid_knowledge_ids(
        self, question_id: str, valid_knowledge_ids: set[str]
    ) -> list[str]:
        links = list(
            self.session.scalars(
                select(QuestionKnowledgeLink).where(
                    QuestionKnowledgeLink.question_id == question_id
                )
            )
        )
        result: list[str] = []
        for link in links:
            if (
                link.knowledge_node_id in valid_knowledge_ids
                and link.knowledge_node_id not in result
            ):
                result.append(link.knowledge_node_id)
        return result

    def _difficulty(self, question_id: str) -> int | None:
        profile = self.session.scalars(
            select(QuestionProfile)
            .where(QuestionProfile.question_id == question_id)
            .order_by(QuestionProfile.profile_version.desc())
        ).first()
        return profile.difficulty if profile is not None else None

    def _chapter_title(
        self,
        curriculum_node_id: str | None,
        curriculum_by_id: dict[str, CurriculumNode],
    ) -> str | None:
        if curriculum_node_id is None:
            return None
        current: str | None = curriculum_node_id
        while current is not None:
            node = curriculum_by_id.get(current)
            if node is None:
                return None
            if node.node_type == "chapter":
                return node.title
            current = node.parent_id
        return None

    def _not_found_message(self, feedback) -> str:
        if feedback.address is not None:
            return (
                f"当前试卷中没有“{feedback.address.section_type}"
                f"第{feedback.address.section_order}题”，请确认题号。"
            )
        return f"当前试卷中没有全卷第{feedback.position}题，请确认题号。"
