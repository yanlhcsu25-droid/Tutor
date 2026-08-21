"""Execute a confirmed TeachingDesign through the existing generation engine.

T2 keeps the existing GenerationService/CP-SAT implementation and strengthens
the compilation contract instead of creating a second selector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from calculus_agent.agent.conversation_state import PendingGeneration
from calculus_agent.agent.schemas import GenerationPlanPatch, GenerationPlanPreview
from calculus_agent.agent.services.generation import GenerationService
from calculus_agent.agent.state.service import WorkspaceService
from calculus_agent.agent.tools.paper_tools import (
    GeneratePaperToolResult,
    resolve_advisory_knowledge_preferences,
)
from calculus_agent.generation_diagnosis import (
    GenerationDiagnosis,
    diagnose_generation_error,
)
from calculus_agent.teaching_design.generation_adapter import (
    GenerationProjection,
    project_confirmed_design,
)
from calculus_agent.teaching_design.schemas import TeachingDesignRead


class GenerationStore(Protocol):
    def get_generation(self, conversation_id: str) -> PendingGeneration | None: ...
    def clear_generation(self, conversation_id: str) -> None: ...
    def get_memory(self, conversation_id: str): ...
    def set_memory(self, conversation_id: str, memory) -> None: ...


class TeachingDesignPaperGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    ok: bool
    code: str | None = None
    teaching_design_version_id: str
    projection: GenerationProjection | None = None
    generation_preview: GenerationPlanPreview | None = None
    paper: GeneratePaperToolResult | None = None

    requires_design_revision: bool = False
    unsupported_design_constraints: list[str] = Field(default_factory=list)
    advisory_constraints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    diagnosis: GenerationDiagnosis | None = None

    @model_validator(mode="after")
    def derive_design_revision_from_diagnosis(self) -> "TeachingDesignPaperGenerationResult":
        """Diagnosis is the sole authority for design-revision semantics."""
        self.requires_design_revision = bool(
            self.diagnosis is not None
            and self.diagnosis.recoverability == "requires_design_revision"
        )
        return self


@dataclass
class TeachingDesignPaperGenerationService:
    session: Session
    store: GenerationStore | None
    conversation_id: str | None

    def _cleanup_internal_pending(
        self,
        design_version_id: str,
        *,
        failed: bool,
    ) -> None:
        if self.store is None or not self.conversation_id:
            return
        self.store.clear_generation(self.conversation_id)
        try:
            memory = self.store.get_memory(self.conversation_id)
            memory.active_task = {
                "type": "teaching_design_generation",
                "status": "failed" if failed else "completed",
                "teaching_design_version_id": design_version_id,
            }
            if failed:
                memory.generation_summary = {}
            self.store.set_memory(self.conversation_id, memory)
        except Exception:
            pass

    def execute(
        self,
        design: TeachingDesignRead,
    ) -> TeachingDesignPaperGenerationResult:
        projection = project_confirmed_design(design)

        if (
            self.store is None
            or not self.conversation_id
            or not all(
                hasattr(self.store, name)
                for name in (
                    "get_generation",
                    "clear_generation",
                    "get_memory",
                    "set_memory",
                )
            )
        ):
            return TeachingDesignPaperGenerationResult(
                ok=False,
                code="generation_state_store_unavailable",
                teaching_design_version_id=design.version_id,
                projection=projection,
                blocking_errors=["generation_state_store_unavailable"],
                clarification_questions=[
                    "当前运行环境缺少可持久化的 generation state store，"
                    "不能安全执行已确认教学设计。"
                ],
                diagnosis=diagnose_generation_error(
                    "generation_state_store_unavailable"
                ),
            )

        if projection.unsupported_design_constraints:
            return TeachingDesignPaperGenerationResult(
                ok=False,
                code="teaching_design_not_executable",
                teaching_design_version_id=design.version_id,
                projection=projection,
                requires_design_revision=True,
                unsupported_design_constraints=list(
                    projection.unsupported_design_constraints
                ),
                advisory_constraints=list(
                    projection.advisory_constraints
                ),
                blocking_errors=["teaching_design_not_executable"],
                clarification_questions=[
                    "当前教学设计仍包含执行层无法安全表示的约束。"
                    "系统不会静默删除这些约束直接出卷。"
                ],
                diagnosis=diagnose_generation_error(
                    "teaching_design_not_executable"
                ),
            )

        existing = self.store.get_generation(self.conversation_id)
        if existing is not None:
            return TeachingDesignPaperGenerationResult(
                ok=False,
                code="pending_generation_exists",
                teaching_design_version_id=design.version_id,
                projection=projection,
                advisory_constraints=list(
                    projection.advisory_constraints
                ),
                blocking_errors=["pending_generation_exists"],
                clarification_questions=[
                    "当前仍存在旧的待确认组卷方案，不能与已确认教学设计静默合并。"
                    "请先处理旧方案后再执行该教学设计。"
                ],
                diagnosis=diagnose_generation_error(
                    "pending_generation_exists"
                ),
            )

        # Only assessment_required_knowledge is compiled as a hard coverage
        # constraint. Teaching-plan prose is resolved best-effort and can never
        # turn an otherwise valid confirmed design into knowledge_unknown.
        payload = dict(projection.payload)
        resolved_advisory, advisory_knowledge_warnings = (
            resolve_advisory_knowledge_preferences(
                self.session,
                projection.advisory_knowledge_names,
                scope_labels=design.content.scope_names,
            )
        )
        payload["knowledge_preferences"] = resolved_advisory
        payload["knowledge_priority_weights"] = {
            name: weight
            for name, weight in (payload.get("knowledge_priority_weights") or {}).items()
            if name in resolved_advisory
        }
        patch = GenerationPlanPatch.model_validate(payload)
        service = GenerationService(
            session=self.session,
            store=self.store,
            conversation_id=self.conversation_id,
            teaching_design_version_id=design.version_id,
            workspace_service=WorkspaceService(self.session),
        )
        preview = service.preview(patch)

        advisory_warnings = [
            f"teaching_design_advisory:{item}"
            for item in projection.advisory_constraints
        ]
        advisory_warnings.extend(advisory_knowledge_warnings)

        if not preview.ok:
            self._cleanup_internal_pending(
                design.version_id,
                failed=True,
            )
            return TeachingDesignPaperGenerationResult(
                ok=False,
                code="teaching_design_generation_preview_failed",
                teaching_design_version_id=design.version_id,
                projection=projection,
                generation_preview=preview,
                requires_design_revision=True,
                advisory_constraints=list(
                    projection.advisory_constraints
                ),
                warnings=list(
                    dict.fromkeys(
                        [*preview.warnings, *advisory_warnings]
                    )
                ),
                blocking_errors=list(preview.blocking_errors),
                clarification_questions=list(
                    preview.clarification_questions
                ),
                diagnosis=diagnose_generation_error(
                    preview.blocking_errors[0]
                    if preview.blocking_errors
                    else "unknown_generation_failure"
                ),
            )

        paper = service.confirm()

        if not paper.ok:
            self._cleanup_internal_pending(
                design.version_id,
                failed=True,
            )
            return TeachingDesignPaperGenerationResult(
                ok=False,
                code="teaching_design_generation_failed",
                teaching_design_version_id=design.version_id,
                projection=projection,
                generation_preview=preview,
                paper=paper,
                requires_design_revision=False,
                advisory_constraints=list(
                    projection.advisory_constraints
                ),
                warnings=list(
                    dict.fromkeys(
                        [
                            *preview.warnings,
                            *paper.warnings,
                            *advisory_warnings,
                        ]
                    )
                ),
                blocking_errors=list(paper.blocking_errors),
                clarification_questions=list(
                    paper.clarification_questions
                ),
                diagnosis=paper.diagnosis or diagnose_generation_error(
                    paper.blocking_errors[0]
                    if paper.blocking_errors
                    else "unknown_generation_failure"
                ),
            )

        return TeachingDesignPaperGenerationResult(
            ok=True,
            teaching_design_version_id=design.version_id,
            projection=projection,
            generation_preview=preview,
            paper=paper,
            advisory_constraints=list(
                projection.advisory_constraints
            ),
            warnings=list(
                dict.fromkeys(
                    [
                        *preview.warnings,
                        *paper.warnings,
                        *advisory_warnings,
                    ]
                )
            ),
        )
