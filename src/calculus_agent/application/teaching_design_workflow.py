"""Deterministic creation workflow for a TeachingDesign.

The workflow owns only orchestration.  Curriculum resolution, environment
inspection, and TeachingDesign persistence remain delegated to their existing
application/domain services.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from calculus_agent.application.curriculum_retrieval import retrieve_curriculum_candidates
from calculus_agent.application.scope_resolution import resolve_deterministic_scope_labels
from calculus_agent.application.teaching_environment import (
    InspectQuestionBankRequest,
    inspect_curriculum,
    inspect_question_bank,
)
from calculus_agent.teaching_design.schemas import EvidenceReference, TeachingDesignContent, TeachingDesignRead
from calculus_agent.teaching_design.service import TeachingDesignService


class TeachingRequirement(BaseModel):
    """LLM-understood semantic requirement consumed by the deterministic flow.

    ``content`` deliberately remains the existing TeachingDesign semantic
    contract: the workflow does not become a second source of truth or invent
    teaching content.  The remaining fields make the intent observable to
    callers and are useful to a future requirement parser.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=1000)
    student_problem: str | None = Field(default=None, max_length=3000)
    goal: str | None = Field(default=None, max_length=3000)
    content: TeachingDesignContent


class TeachingDesignWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: str
    teaching_design: TeachingDesignRead | None = None
    code: str | None = None
    message: str | None = None
    curriculum_candidates: list[dict[str, Any]] = Field(default_factory=list)
    resolved_scope_names: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)


class TeachingDesignWorkflow:
    """Execute the fixed evidence-before-create TeachingDesign sequence."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def execute(
        self,
        *,
        requirement: TeachingRequirement,
        owner_key: str,
        conversation_id: str | None,
        run_id: str | None,
        source_user_message: str,
    ) -> TeachingDesignWorkflowResult:
        if not conversation_id:
            return self._failure(
                "teaching_design_requires_conversation",
                "教学设计需要明确的 conversation_id 才能持久化。",
            )

        # Retrieval is retained as part of the workflow's fixed evidence path.
        # Scope authority stays with the existing deterministic resolver.
        candidates = [
            item.model_dump(mode="json")
            for item in retrieve_curriculum_candidates(
                self.session, query=requirement.topic, top_k=5
            )
        ]
        resolved = resolve_deterministic_scope_labels(
            self.session, requirement.content.scope_names
        )
        if not resolved.ok:
            return self._failure(
                "scope_resolution_required",
                "请先确认教材章节或知识点范围，再创建教学设计。",
                curriculum_candidates=candidates,
            )

        scope_names = resolved.validated_scope_names
        curriculum = inspect_curriculum(
            self.session, scope_names=scope_names, run_id=run_id
        )
        if not curriculum.ok:
            return self._failure(
                "curriculum_scope_unresolved",
                "部分教学范围无法映射到当前激活教材目录，请确认章节名称。",
                curriculum_candidates=candidates,
                resolved_scope_names=scope_names,
            )

        question_bank = inspect_question_bank(
            self.session,
            InspectQuestionBankRequest(scope_names=scope_names, detail_level="aggregate"),
            run_id=run_id,
        )
        if not question_bank.ok:
            return self._failure(
                "question_bank_scope_unresolved",
                "题库调查范围无法唯一映射到当前教材章节，请先确认范围。",
                curriculum_candidates=candidates,
                resolved_scope_names=scope_names,
            )

        evidence_refs = [
            ref for ref in (curriculum.evidence_ref, question_bank.evidence_ref)
            if ref is not None
        ]
        service = TeachingDesignService(self.session)
        active = service.get_active(owner_key=owner_key, conversation_id=conversation_id)
        if active is not None and active.status in {"draft", "awaiting_confirmation"}:
            return self._failure(
                "active_teaching_design_exists",
                "当前已有未确认教学设计；应修改现有版本，而不是静默创建另一份设计。",
                curriculum_candidates=candidates,
                resolved_scope_names=scope_names,
                evidence_refs=evidence_refs,
            )

        content = requirement.content.model_copy(update={
            "scope_names": scope_names,
            "evidence_refs": evidence_refs,
        })
        design = service.create(
            owner_key=owner_key,
            conversation_id=conversation_id,
            content=content,
            run_id=run_id,
            source_user_message=source_user_message,
            change_reason="teacher_requested_new_teaching_design",
            ready_for_confirmation=True,
        )
        return TeachingDesignWorkflowResult(
            ok=True,
            status="waiting_confirmation",
            teaching_design=design,
            curriculum_candidates=candidates,
            resolved_scope_names=scope_names,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _failure(code: str, message: str, **values: Any) -> TeachingDesignWorkflowResult:
        return TeachingDesignWorkflowResult(
            ok=False, status="needs_clarification", code=code, message=message, **values
        )
