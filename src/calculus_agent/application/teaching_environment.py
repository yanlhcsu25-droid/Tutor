"""Deterministic curriculum/question-bank inspection for TeachingDesign.

This module is a cross-domain read model:
- curriculum/chapter ownership comes from the existing knowledge/question layer;
- paper supply uses the exact shared teacher-facing eligibility predicate;
- no LLM interpretation happens here;
- every successful observation returns a compact EvidenceReference that can be
  persisted inside an immutable TeachingDesign version.

Full observations remain in TeacherAgentRunTrace.tool_calls_json. The compact
EvidenceReference stores enough provenance to verify which run produced the
design decision without copying the whole question bank into TeachingDesign.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)
from calculus_agent.question_types import (
    PAPER_QUESTION_TYPES,
    canonical_question_type,
)
from calculus_agent.questions.chapter_assignment import (
    chapter_display_name,
    list_active_chapters,
    resolve_chapter_reference,
)
from calculus_agent.questions.eligibility import (
    paper_candidate_statement,
)
from calculus_agent.teaching_design.schemas import EvidenceReference


class CurriculumChapterObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    title: str
    section_titles: list[str] = Field(default_factory=list)
    curriculum_node_count: int = 0
    knowledge_point_count: int = 0
    knowledge_point_sample: list[str] = Field(default_factory=list)
    knowledge_points_truncated: int = 0


class CurriculumInspectionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    requested_scope_names: list[str]
    resolved_scope_names: list[str] = Field(default_factory=list)
    unresolved_scope_names: list[str] = Field(default_factory=list)
    active_textbook_name: str | None = None
    chapters: list[CurriculumChapterObservation] = Field(default_factory=list)
    evidence_ref: EvidenceReference | None = None


class KnowledgeSupplyObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    total_questions: int
    question_type_counts: dict[str, int] = Field(default_factory=dict)
    difficulty_counts: dict[str, int] = Field(default_factory=dict)


class ChapterQuestionSupplyObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    title: str
    total_questions: int
    question_type_counts: dict[str, int] = Field(default_factory=dict)
    profiled_questions: int = 0
    profile_coverage_ratio: float = 0.0
    difficulty_counts: dict[str, int] = Field(default_factory=dict)
    estimated_time_min_median: float | None = None
    estimated_time_min_range: list[int] = Field(default_factory=list)
    top_knowledge_supply: list[KnowledgeSupplyObservation] = Field(
        default_factory=list
    )


class QuestionBankInspectionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    detail_level: Literal["aggregate", "chapter_detail"]
    requested_scope_names: list[str]
    resolved_scope_names: list[str] = Field(default_factory=list)
    unresolved_scope_names: list[str] = Field(default_factory=list)
    chapter_name: str | None = None

    total_questions: int = 0
    profiled_questions: int = 0
    profile_coverage_ratio: float = 0.0
    question_type_counts: dict[str, int] = Field(default_factory=dict)
    difficulty_counts: dict[str, int] = Field(default_factory=dict)
    chapters: list[ChapterQuestionSupplyObservation] = Field(
        default_factory=list
    )

    evidence_ref: EvidenceReference | None = None


class InspectQuestionBankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_names: list[str] = Field(min_length=1, max_length=20)
    detail_level: Literal["aggregate", "chapter_detail"] = "aggregate"
    chapter_name: str | None = Field(default=None, max_length=255)
    knowledge_names: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def detail_requires_chapter(self) -> "InspectQuestionBankRequest":
        if self.detail_level == "chapter_detail" and not self.chapter_name:
            raise ValueError(
                "chapter_detail requires chapter_name"
            )
        if self.detail_level == "aggregate" and self.chapter_name:
            raise ValueError(
                "chapter_name is only valid for chapter_detail"
            )
        return self


def _fingerprint(kind: str, payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"{kind}:{digest}"


def _active_textbook_name(session: Session) -> str | None:
    return session.scalar(
        select(Textbook.name)
        .where(Textbook.is_active.is_(True))
        .order_by(Textbook.created_at.desc(), Textbook.id)
        .limit(1)
    )


def _resolve_scope(
    session: Session,
    labels: list[str],
) -> tuple[list[CurriculumNode], list[str]]:
    resolved: list[CurriculumNode] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    for raw in labels:
        label = raw.strip()
        if not label:
            continue
        chapter = resolve_chapter_reference(
            session,
            label=label,
        )
        if chapter is None:
            unresolved.append(label)
            continue
        if chapter.id not in seen:
            resolved.append(chapter)
            seen.add(chapter.id)

    return resolved, unresolved


def _descendant_nodes(
    all_nodes: list[CurriculumNode],
    root_id: str,
) -> list[CurriculumNode]:
    children: dict[str | None, list[CurriculumNode]] = defaultdict(list)
    for node in all_nodes:
        children[node.parent_id].append(node)

    result: list[CurriculumNode] = []
    queue = list(children.get(root_id, []))
    while queue:
        current = queue.pop(0)
        result.append(current)
        queue.extend(children.get(current.id, []))
    return result


def inspect_curriculum(
    session: Session,
    *,
    scope_names: list[str],
    run_id: str | None,
) -> CurriculumInspectionRead:
    requested = list(
        dict.fromkeys(
            value.strip()
            for value in scope_names
            if value and value.strip()
        )
    )
    chapters, unresolved = _resolve_scope(
        session,
        requested,
    )
    resolved_names = [
        chapter_display_name(chapter) or chapter.title
        for chapter in chapters
    ]

    if unresolved or not chapters:
        return CurriculumInspectionRead(
            ok=False,
            requested_scope_names=requested,
            resolved_scope_names=resolved_names,
            unresolved_scope_names=unresolved,
            active_textbook_name=_active_textbook_name(session),
        )

    active_chapters = list_active_chapters(session)
    textbook_ids = {
        chapter.textbook_id
        for chapter in active_chapters
        if chapter.textbook_id
    }
    node_statement = select(CurriculumNode).where(
        CurriculumNode.review_status == "approved"
    )
    if textbook_ids:
        node_statement = node_statement.where(
            CurriculumNode.textbook_id.in_(textbook_ids)
        )
    all_nodes = list(
        session.scalars(
            node_statement.order_by(
                CurriculumNode.sort_order,
                CurriculumNode.id,
            )
        ).all()
    )

    observations: list[CurriculumChapterObservation] = []
    for chapter in chapters:
        descendants = _descendant_nodes(
            all_nodes,
            chapter.id,
        )
        curriculum_ids = [
            chapter.id,
            *[node.id for node in descendants],
        ]

        section_titles = [
            node.title
            for node in descendants
            if node.node_type == "section"
        ]

        knowledge = list(
            session.scalars(
                select(KnowledgeNode)
                .where(
                    KnowledgeNode.curriculum_node_id.in_(
                        curriculum_ids
                    ),
                    KnowledgeNode.review_status == "approved",
                )
                .order_by(
                    KnowledgeNode.name,
                    KnowledgeNode.id,
                )
            ).all()
        )
        unique_names = list(
            dict.fromkeys(
                item.name.strip()
                for item in knowledge
                if item.name and item.name.strip()
            )
        )
        sample = unique_names[:20]

        observations.append(
            CurriculumChapterObservation(
                chapter_id=chapter.id,
                title=(
                    chapter_display_name(chapter)
                    or chapter.title
                ),
                section_titles=section_titles[:20],
                curriculum_node_count=1 + len(descendants),
                knowledge_point_count=len(unique_names),
                knowledge_point_sample=sample,
                knowledge_points_truncated=max(
                    0,
                    len(unique_names) - len(sample),
                ),
            )
        )

    summary_parts = [
        (
            f"{item.title}: "
            f"{len(item.section_titles)}个节，"
            f"{item.knowledge_point_count}个已审核知识节点"
        )
        for item in observations
    ]
    summary = "；".join(summary_parts)

    payload = {
        "requested_scope_names": requested,
        "resolved_scope_names": resolved_names,
        "chapters": [
            item.model_dump(mode="json")
            for item in observations
        ],
    }
    evidence = EvidenceReference(
        kind="curriculum_scope",
        ref_id=_fingerprint(
            "curriculum_scope",
            payload,
        ),
        summary=summary[:3000],
        observed_by_run_id=run_id,
    )

    return CurriculumInspectionRead(
        ok=True,
        requested_scope_names=requested,
        resolved_scope_names=resolved_names,
        unresolved_scope_names=[],
        active_textbook_name=_active_textbook_name(session),
        chapters=observations,
        evidence_ref=evidence,
    )


def _latest_approved_profiles(
    session: Session,
    question_ids: list[str],
) -> dict[str, QuestionProfile]:
    if not question_ids:
        return {}
    rows = list(
        session.scalars(
            select(QuestionProfile)
            .where(
                QuestionProfile.question_id.in_(
                    question_ids
                ),
                QuestionProfile.profile_status == "approved",
            )
            .order_by(
                QuestionProfile.question_id,
                QuestionProfile.profile_version.desc(),
            )
        ).all()
    )
    result: dict[str, QuestionProfile] = {}
    for profile in rows:
        result.setdefault(
            profile.question_id,
            profile,
        )
    return result


def _knowledge_by_question(
    session: Session,
    question_ids: list[str],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    if not question_ids:
        return result

    for question_id, name in session.execute(
        select(
            QuestionKnowledgeLink.question_id,
            KnowledgeNode.name,
        )
        .join(
            KnowledgeNode,
            KnowledgeNode.id
            == QuestionKnowledgeLink.knowledge_node_id,
        )
        .where(
            QuestionKnowledgeLink.question_id.in_(
                question_ids
            ),
            KnowledgeNode.review_status == "approved",
        )
    ):
        result[question_id].append(name)

    return result


def _difficulty_counts(
    profiles: list[QuestionProfile],
) -> dict[str, int]:
    counts = Counter(
        str(profile.difficulty)
        for profile in profiles
    )
    return {
        key: counts[key]
        for key in sorted(counts)
    }


def _knowledge_supply(
    *,
    questions: list[Question],
    profiles: dict[str, QuestionProfile],
    knowledge_by_question: dict[str, list[str]],
    focus_names: list[str],
    limit: int,
) -> list[KnowledgeSupplyObservation]:
    question_ids_by_name: dict[str, set[str]] = defaultdict(set)
    for question in questions:
        for name in knowledge_by_question.get(
            question.id,
            [],
        ):
            question_ids_by_name[name].add(question.id)

    names = list(question_ids_by_name)
    focus = {
        item.strip()
        for item in focus_names
        if item and item.strip()
    }
    if focus:
        names.sort(
            key=lambda name: (
                0 if name in focus else 1,
                -len(question_ids_by_name[name]),
                name,
            )
        )
    else:
        names.sort(
            key=lambda name: (
                -len(question_ids_by_name[name]),
                name,
            )
        )

    result: list[KnowledgeSupplyObservation] = []
    for name in names[:limit]:
        question_ids = question_ids_by_name[name]
        rows = [
            question
            for question in questions
            if question.id in question_ids
        ]
        type_counts = Counter(
            canonical_question_type(
                question.question_type
            )
            for question in rows
        )
        row_profiles = [
            profiles[question.id]
            for question in rows
            if question.id in profiles
        ]
        result.append(
            KnowledgeSupplyObservation(
                name=name,
                total_questions=len(question_ids),
                question_type_counts=dict(
                    sorted(type_counts.items())
                ),
                difficulty_counts=_difficulty_counts(
                    row_profiles
                ),
            )
        )
    return result


def _chapter_supply(
    *,
    chapter: CurriculumNode,
    questions: list[Question],
    profiles: dict[str, QuestionProfile],
    knowledge_by_question: dict[str, list[str]],
    focus_knowledge_names: list[str],
    detail: bool,
) -> ChapterQuestionSupplyObservation:
    chapter_questions = [
        question
        for question in questions
        if question.curriculum_chapter_id == chapter.id
    ]
    type_counts = Counter(
        canonical_question_type(question.question_type)
        for question in chapter_questions
    )
    chapter_profiles = [
        profiles[question.id]
        for question in chapter_questions
        if question.id in profiles
    ]
    times = sorted(
        profile.estimated_time_min
        for profile in chapter_profiles
    )

    return ChapterQuestionSupplyObservation(
        chapter_id=chapter.id,
        title=(
            chapter_display_name(chapter)
            or chapter.title
        ),
        total_questions=len(chapter_questions),
        question_type_counts=dict(
            sorted(type_counts.items())
        ),
        profiled_questions=len(chapter_profiles),
        profile_coverage_ratio=(
            round(
                len(chapter_profiles)
                / len(chapter_questions),
                3,
            )
            if chapter_questions
            else 0.0
        ),
        difficulty_counts=_difficulty_counts(
            chapter_profiles
        ),
        estimated_time_min_median=(
            float(median(times))
            if times
            else None
        ),
        estimated_time_min_range=(
            [min(times), max(times)]
            if times
            else []
        ),
        top_knowledge_supply=_knowledge_supply(
            questions=chapter_questions,
            profiles=profiles,
            knowledge_by_question=knowledge_by_question,
            focus_names=focus_knowledge_names,
            limit=30 if detail else 8,
        ),
    )


def inspect_question_bank(
    session: Session,
    request: InspectQuestionBankRequest,
    *,
    run_id: str | None,
) -> QuestionBankInspectionRead:
    requested = list(
        dict.fromkeys(
            item.strip()
            for item in request.scope_names
            if item and item.strip()
        )
    )
    chapters, unresolved = _resolve_scope(
        session,
        requested,
    )
    resolved_names = [
        chapter_display_name(chapter) or chapter.title
        for chapter in chapters
    ]

    if unresolved or not chapters:
        return QuestionBankInspectionRead(
            ok=False,
            detail_level=request.detail_level,
            requested_scope_names=requested,
            resolved_scope_names=resolved_names,
            unresolved_scope_names=unresolved,
            chapter_name=request.chapter_name,
        )

    selected_chapters = chapters
    detail_chapter: CurriculumNode | None = None
    if request.detail_level == "chapter_detail":
        detail_chapter = resolve_chapter_reference(
            session,
            label=request.chapter_name,
        )
        if (
            detail_chapter is None
            or detail_chapter.id
            not in {item.id for item in chapters}
        ):
            return QuestionBankInspectionRead(
                ok=False,
                detail_level=request.detail_level,
                requested_scope_names=requested,
                resolved_scope_names=resolved_names,
                unresolved_scope_names=[
                    request.chapter_name or ""
                ],
                chapter_name=request.chapter_name,
            )
        selected_chapters = [detail_chapter]

    chapter_ids = [item.id for item in selected_chapters]
    statement = paper_candidate_statement().where(
        Question.curriculum_chapter_id.in_(
            chapter_ids
        )
    )
    candidate_questions = [
        question
        for question in session.scalars(
            statement.order_by(
                Question.created_at,
                Question.id,
            )
        ).all()
        if canonical_question_type(
            question.question_type
        ) in PAPER_QUESTION_TYPES
    ]

    question_ids = [
        question.id
        for question in candidate_questions
    ]
    profiles = _latest_approved_profiles(
        session,
        question_ids,
    )
    knowledge = _knowledge_by_question(
        session,
        question_ids,
    )

    observations = [
        _chapter_supply(
            chapter=chapter,
            questions=candidate_questions,
            profiles=profiles,
            knowledge_by_question=knowledge,
            focus_knowledge_names=request.knowledge_names,
            detail=(
                request.detail_level
                == "chapter_detail"
            ),
        )
        for chapter in selected_chapters
    ]

    total = len(candidate_questions)
    profiled = len(profiles)
    type_counts = Counter(
        canonical_question_type(question.question_type)
        for question in candidate_questions
    )
    all_profiles = list(profiles.values())

    result_payload = {
        "detail_level": request.detail_level,
        "requested_scope_names": requested,
        "resolved_scope_names": resolved_names,
        "chapter_name": request.chapter_name,
        "total_questions": total,
        "profiled_questions": profiled,
        "question_type_counts": dict(
            sorted(type_counts.items())
        ),
        "difficulty_counts": _difficulty_counts(
            all_profiles
        ),
        "chapters": [
            item.model_dump(mode="json")
            for item in observations
        ],
    }

    if request.detail_level == "aggregate":
        summary = "；".join(
            (
                f"{item.title}: {item.total_questions}题，"
                f"题型{item.question_type_counts}，"
                f"画像{item.profiled_questions}/"
                f"{item.total_questions}"
            )
            for item in observations
        )
        kind = "question_bank_aggregate"
    else:
        item = observations[0]
        summary = (
            f"{item.title}: {item.total_questions}题，"
            f"题型{item.question_type_counts}，"
            f"难度{item.difficulty_counts}，"
            f"知识点供给"
            f"{[(x.name, x.total_questions) for x in item.top_knowledge_supply]}"
        )
        kind = "question_bank_detail"

    evidence = EvidenceReference(
        kind=kind,
        ref_id=_fingerprint(
            kind,
            result_payload,
        ),
        summary=summary[:3000],
        observed_by_run_id=run_id,
    )

    return QuestionBankInspectionRead(
        ok=True,
        detail_level=request.detail_level,
        requested_scope_names=requested,
        resolved_scope_names=resolved_names,
        unresolved_scope_names=[],
        chapter_name=(
            (
                chapter_display_name(detail_chapter)
                or detail_chapter.title
            )
            if detail_chapter is not None
            else None
        ),
        total_questions=total,
        profiled_questions=profiled,
        profile_coverage_ratio=(
            round(profiled / total, 3)
            if total
            else 0.0
        ),
        question_type_counts=dict(
            sorted(type_counts.items())
        ),
        difficulty_counts=_difficulty_counts(
            all_profiles
        ),
        chapters=observations,
        evidence_ref=evidence,
    )
