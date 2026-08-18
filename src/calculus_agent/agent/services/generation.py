"""Generation application service shared by Agent tools and HTTP adapters.

Phase 4B-1 extracts the generation use case from ``tool_registry.py`` without
changing business behavior.  This service owns deterministic merge/rebalance,
pending-generation persistence and working-memory projection.  It deliberately
does not know about Agent Tool wrappers, model calls, or HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session

from calculus_agent.models import Paper
from calculus_agent.question_types import canonical_question_type

from ..conversation_state import PendingGeneration, PendingGenerationStaleError
from ..schemas import (
    AgentWorkingMemory,
    GenerationPlanPatch,
    GenerationPlanPreview,
    GeneratePaperInput,
    QuestionTypeRequirement,
)
from ..tools.paper_tools import (
    GeneratePaperToolResult,
    build_structured_generation_request,
    generate_paper_from_input,
)


class GenerationStateStore(Protocol):
    def get_generation(self, conversation_id: str) -> PendingGeneration | None: ...
    def set_generation(
        self,
        conversation_id: str,
        pending: PendingGeneration,
        *,
        expected_version: int | None = None,
    ) -> PendingGeneration: ...
    def clear_generation(self, conversation_id: str) -> None: ...
    def get_memory(self, conversation_id: str) -> AgentWorkingMemory: ...
    def set_memory(self, conversation_id: str, memory: AgentWorkingMemory) -> None: ...


class NoPendingGenerationError(LookupError):
    """Raised when confirmation is requested without a persisted pending plan."""


def _normalized_generation_request(
    request: GeneratePaperInput,
    generation_request,
) -> GeneratePaperInput:
    blueprint = generation_request.blueprint
    return request.model_copy(update={
        "question_count": blueprint.total_questions,
        "total_score": int(blueprint.total_score),
        "question_type_requirements": [
            QuestionTypeRequirement(
                question_type=section.question_type,
                count=section.count,
                score_each=section.score_per_question,
                total_score=section.total_score,
            )
            for section in blueprint.sections
        ] if blueprint.sections else [
            QuestionTypeRequirement(question_type=name, count=count)
            for name, count in blueprint.question_type_counts.items()
        ],
    })


def _derive_question_count(request: GeneratePaperInput) -> GeneratePaperInput:
    """Make complete per-type counts the only source for pending question_count."""
    requirements = request.question_type_requirements or []
    if not requirements:
        return request
    return request.model_copy(update={
        "question_count": sum(item.count for item in requirements),
    })


def _same_optional_score(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return isclose(left, right)


def _requirements_match_pending(
    pending_request: GeneratePaperInput,
    incoming: list[QuestionTypeRequirement] | None,
) -> bool:
    """Return True only when a full requirement list is a deterministic no-op."""
    if incoming is None:
        return False

    current_items = pending_request.question_type_requirements or []
    if len(incoming) != len(current_items):
        return False

    current: dict[str, QuestionTypeRequirement] = {}
    for item in current_items:
        name = canonical_question_type(item.question_type)
        if name in current:
            return False
        current[name] = item

    seen: set[str] = set()
    for item in incoming:
        name = canonical_question_type(item.question_type)
        if name in seen:
            return False
        seen.add(name)
        previous = current.get(name)
        if previous is None:
            return False
        if item.count != previous.count:
            return False
        if not _same_optional_score(item.score_each, previous.score_each):
            return False

    return seen == set(current)


def _merge_question_type_patch(
    base: GeneratePaperInput,
    patch_values: dict,
) -> tuple[GeneratePaperInput, set[str], set[str]]:
    """Merge a deterministic partial type patch into the authoritative base."""
    current = {
        item.question_type: item.model_copy()
        for item in (base.question_type_requirements or [])
    }
    incoming = patch_values.pop("question_type_patches", None)
    explicit_partial_patch = incoming is not None
    if incoming is None and "question_type_requirements" in patch_values:
        incoming = patch_values.pop("question_type_requirements") or []

    changed_counts: set[str] = set()
    changed_scores: set[str] = set()

    for raw in incoming or []:
        values = (
            raw.model_dump(exclude_unset=True)
            if isinstance(raw, BaseModel)
            else dict(raw)
        )
        name = canonical_question_type(values["question_type"])
        values["question_type"] = name
        previous = current.get(name)

        if previous is None:
            current[name] = QuestionTypeRequirement.model_validate(values)
            changed_counts.add(name)
            if values.get("score_each") is not None:
                changed_scores.add(name)
            continue

        updates = {}
        if "count" in values and values["count"] != previous.count:
            updates["count"] = values["count"]
            changed_counts.add(name)

        if "score_each" in values and values["score_each"] is not None:
            if explicit_partial_patch or not isclose(
                values["score_each"],
                previous.score_each or 0,
            ):
                changed_scores.add(name)
            if not isclose(values["score_each"], previous.score_each or 0):
                updates["score_each"] = values["score_each"]

        if updates:
            updates["total_score"] = (
                updates.get("count", previous.count)
                * updates.get("score_each", previous.score_each)
            )
            current[name] = previous.model_copy(update=updates)

    merged = _derive_question_count(base.model_copy(update={
        **patch_values,
        "question_type_requirements": list(current.values()),
    }))
    return merged, changed_counts, changed_scores


def _rebalance_scores(
    request: GeneratePaperInput,
    *,
    locked_types: set[str],
    changed_count_types: set[str],
) -> tuple[GeneratePaperInput | None, str | None]:
    requirements = [
        item.model_copy()
        for item in (request.question_type_requirements or [])
    ]
    if (
        not requirements
        or request.total_score is None
        or any(item.score_each is None for item in requirements)
    ):
        return request, None

    current_total = sum(item.count * item.score_each for item in requirements)
    difference = request.total_score - current_total

    if isclose(difference, 0):
        normalized = [
            item.model_copy(update={
                "total_score": item.count * item.score_each,
            })
            for item in requirements
        ]
        return request.model_copy(update={
            "question_type_requirements": normalized,
            "question_count": sum(item.count for item in normalized),
        }), None

    preferred = ["计算题", "证明题", "填空题", "选择题"]
    candidates = sorted(
        (
            item
            for item in requirements
            if item.question_type not in locked_types
            and item.question_type not in changed_count_types
        ),
        key=lambda item: (
            preferred.index(item.question_type)
            if item.question_type in preferred
            else len(preferred)
        ),
    )

    for item in candidates:
        new_score = item.score_each + difference / item.count
        if new_score > 0 and isclose(new_score * 2, round(new_score * 2)):
            balanced = [
                entry.model_copy(update={
                    "score_each": new_score,
                    "total_score": entry.count * new_score,
                })
                if entry.question_type == item.question_type
                else entry.model_copy(update={
                    "total_score": entry.count * entry.score_each,
                })
                for entry in requirements
            ]
            return request.model_copy(update={
                "question_type_requirements": balanced,
                "question_count": sum(entry.count for entry in balanced),
            }), None

    return (
        None,
        "当前题型数量无法按0.5分粒度自动平衡到目标总分，请明确希望调整哪一类题目的每题分值。",
    )


@dataclass
class GenerationService:
    session: Session
    store: GenerationStateStore | None
    conversation_id: str | None
    expected_pending_generation_version: int | None = None
    teaching_design_version_id: str | None = None

    def _pending(self) -> PendingGeneration | None:
        if self.store is None or not self.conversation_id:
            return None
        return self.store.get_generation(self.conversation_id)

    def _memory(self) -> AgentWorkingMemory:
        if self.store is None or not self.conversation_id:
            return AgentWorkingMemory()
        return self.store.get_memory(self.conversation_id)

    def assert_pending_version(self, expected_version: int) -> PendingGeneration:
        pending = self._pending()
        if pending is None:
            raise NoPendingGenerationError("no_pending_generation")
        if pending.pending_version != expected_version:
            raise PendingGenerationStaleError("stale_pending_plan")
        return pending

    def preview(self, raw_patch: GenerationPlanPatch | BaseModel | dict) -> GenerationPlanPreview:
        patch = GenerationPlanPatch.model_validate(raw_patch)
        patch_values = patch.model_dump(exclude_unset=True)
        unsupported = patch_values.pop("avoid_previous_paper_questions", None)

        pending = self._pending()
        memory = self._memory()

        # Preserve the existing Phase 3 precedence exactly.
        if pending:
            base_request = pending.request
        elif memory.generation_summary:
            base_request = GeneratePaperInput.model_validate(
                memory.generation_summary
            )
        elif (
            patch_values.get("paper_type") is None
            and memory.last_completed_paper
        ):
            base_request = GeneratePaperInput.model_validate({
                key: value
                for key, value in memory.last_completed_paper.items()
                if key in GeneratePaperInput.model_fields
            })
        else:
            base_request = GeneratePaperInput()

        if pending:
            if (
                "question_type_requirements" in patch.model_fields_set
                and "question_type_patches" not in patch.model_fields_set
            ):
                if _requirements_match_pending(
                    pending.request,
                    patch.question_type_requirements,
                ):
                    patch_values.pop("question_type_requirements", None)
                else:
                    return GenerationPlanPreview(
                        ok=False,
                        request=pending.request,
                        total_questions=pending.request.question_count,
                        total_score=pending.request.total_score,
                        sections=pending.request.question_type_requirements or [],
                        blocking_errors=["generation_partial_patch_required"],
                        clarification_questions=[
                            "当前已有待确认方案。真实题型变更必须只提交教师本轮明确修改的字段，并使用 question_type_patches；未提到的题型必须保持不变。"
                        ],
                    )

        request, changed_count_types, changed_score_types = (
            _merge_question_type_patch(base_request, patch_values)
        )

        if pending:
            locked_types = (
                set(pending.locked_score_question_types)
                | changed_score_types
            )
            balanced, balance_question = _rebalance_scores(
                request,
                locked_types=locked_types,
                changed_count_types=changed_count_types,
            )
            if balanced is None:
                preview = GenerationPlanPreview(
                    ok=False,
                    request=request,
                    total_score=request.total_score,
                    sections=request.question_type_requirements or [],
                    blocking_errors=["score_rebalance_ambiguous"],
                    clarification_questions=[balance_question],
                )
                if self.store is not None and self.conversation_id:
                    memory.last_clarification = {
                        "missing_fields": ["score_rebalance_ambiguous"],
                        "questions": [balance_question],
                    }
                    self.store.set_memory(self.conversation_id, memory)
                return preview
            request = balanced

        generation_request, warnings, errors, questions = (
            build_structured_generation_request(self.session, request)
        )
        if generation_request is not None:
            request = _normalized_generation_request(
                request,
                generation_request,
            )

        preview = GenerationPlanPreview(
            ok=generation_request is not None,
            request=request,
            title=(
                generation_request.blueprint.title
                if generation_request
                else None
            ),
            total_questions=(
                generation_request.blueprint.total_questions
                if generation_request
                else None
            ),
            total_score=(
                generation_request.blueprint.total_score
                if generation_request
                else None
            ),
            sections=[
                QuestionTypeRequirement(
                    question_type=section.question_type,
                    count=section.count,
                    score_each=section.score_per_question,
                    total_score=section.total_score,
                )
                for section in (
                    generation_request.blueprint.sections
                    if generation_request
                    else []
                )
            ]
            if generation_request and generation_request.blueprint.sections
            else [
                QuestionTypeRequirement(
                    question_type=question_type,
                    count=count,
                )
                for question_type, count in (
                    generation_request.blueprint.question_type_counts.items()
                    if generation_request
                    else []
                )
            ],
            warnings=warnings,
            blocking_errors=errors,
            clarification_questions=questions,
        )

        if (
            preview.ok
            and self.store is not None
            and self.conversation_id
        ):
            saved_pending = self.store.set_generation(
                self.conversation_id,
                PendingGeneration(
                    request=request,
                    total_score_source=(
                        "teacher_explicit"
                        if "total_score" in patch.model_fields_set
                        else pending.total_score_source
                        if pending
                        else "default_template"
                    ),
                    locked_score_question_types=sorted(
                        (
                            set(pending.locked_score_question_types)
                            if pending
                            else set()
                        )
                        | changed_score_types
                    ),
                    teaching_design_version_id=(
                        self.teaching_design_version_id
                        if self.teaching_design_version_id is not None
                        else pending.teaching_design_version_id
                        if pending
                        else None
                    ),
                ),
                expected_version=self.expected_pending_generation_version,
            )
            preview = preview.model_copy(update={
                "request": saved_pending.request,
                "pending_version": saved_pending.pending_version,
            })

        if self.store is not None and self.conversation_id:
            memory = self.store.get_memory(self.conversation_id)
            memory.active_task = {
                "type": "generation",
                "status": (
                    "awaiting_confirmation"
                    if preview.ok
                    else "drafting"
                ),
            }
            memory.generation_summary = request.model_dump(mode="json")
            memory.last_clarification = (
                {"missing_fields": errors, "questions": questions}
                if questions
                else None
            )
            if unsupported:
                memory.unsupported_preferences = [{
                    "type": "avoid_previous_paper_questions",
                    "source": "teacher_stated",
                    "status": "unsupported",
                    "reference_paper_id": (
                        memory.last_completed_paper or {}
                    ).get("paper_id"),
                }]
                warnings = [
                    *warnings,
                    "avoid_previous_paper_questions_unsupported",
                ]
                preview = preview.model_copy(update={"warnings": warnings})
            self.store.set_memory(self.conversation_id, memory)

        return preview

    def confirm(self) -> GeneratePaperToolResult:
        pending = self._pending()
        if pending is None:
            raise NoPendingGenerationError("no_pending_generation")

        result = generate_paper_from_input(
            self.session,
            pending.request,
        )

        if (
            pending.teaching_design_version_id is not None
            and result.paper_id is not None
        ):
            paper = self.session.get(Paper, str(result.paper_id))
            if paper is not None:
                paper.teaching_design_version_id = (
                    pending.teaching_design_version_id
                )
                self.session.flush()

        if (
            result.ok
            and self.store is not None
            and self.conversation_id
        ):
            self.store.clear_generation(self.conversation_id)
            memory = self.store.get_memory(self.conversation_id)
            memory.active_task = {
                "type": "generation",
                "status": "completed",
            }
            memory.last_completed_paper = {
                "paper_id": str(result.paper_id),
                "version_id": str(result.version_id),
                "teaching_design_version_id": (
                    pending.teaching_design_version_id
                ),
                **pending.request.model_dump(mode="json"),
            }
            memory.generation_summary = {}
            memory.last_clarification = None
            self.store.set_memory(self.conversation_id, memory)

        return result
