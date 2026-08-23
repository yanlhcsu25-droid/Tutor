"""Thin Tool adapters for the TeachingDesign business domain.

T3 adds one important boundary: environment evidence is system-managed.
The model may decide how observations affect the semantic design, but it may
not fabricate or rewrite EvidenceReference provenance.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from calculus_agent.application.scope_resolution import (
    resolve_deterministic_scope_labels,
)
from calculus_agent.application.teaching_design_execution import (
    TeachingDesignPaperGenerationService,
)
from calculus_agent.application.teaching_design_workflow import (
    TeachingDesignWorkflow,
    TeachingRequirement,
)
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.teaching_design.schemas import (
    EvidenceReference,
    TeachingDesignContent,
    TeachingDesignPatch,
)
from calculus_agent.teaching_design.service import (
    StaleTeachingDesignError,
    TeachingDesignNotFoundError,
    TeachingDesignService,
    TeachingDesignStateError,
)


class CreateTeachingDesignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: TeachingDesignContent


class ReviseTeachingDesignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: TeachingDesignPatch
    change_reason: str = Field(min_length=1, max_length=1000)


class SearchTeachingDesignHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)


class ActivateTeachingDesignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=36)


def teaching_design_tool_names(
    active_design: dict[str, Any] | None,
) -> list[str]:
    common = [
        "search_teaching_design_history",
        "activate_teaching_design",
    ]
    if active_design is None:
        return [
            "create_teaching_design",
            *common,
        ]

    status = active_design.get("status")
    if status == "awaiting_confirmation":
        return [
            "read_active_teaching_design",
            "revise_teaching_design",
            "confirm_teaching_design",
            "discard_teaching_design",
            *common,
        ]
    if status == "draft":
        return [
            "read_active_teaching_design",
            "revise_teaching_design",
            *common,
        ]
    if status == "confirmed":
        return [
            "read_active_teaching_design",
            "revise_teaching_design",
            "create_teaching_design",
            *common,
        ]
    return [
        "read_active_teaching_design",
        "create_teaching_design",
        *common,
    ]


def _normalized_scope(values: list[str]) -> set[str]:
    return {
        normalize_name(value)
        for value in values
        if value and normalize_name(value)
    }


def _trusted_evidence_entries(context: Any) -> list[dict[str, Any]]:
    return list(
        getattr(context, "observed_evidence", {}).values()
    )


def _trusted_evidence_refs(
    context: Any,
    *,
    scope_names: list[str] | None = None,
) -> list[EvidenceReference]:
    required_scope = (
        _normalized_scope(scope_names or [])
        if scope_names is not None
        else set()
    )
    result: list[EvidenceReference] = []
    for entry in _trusted_evidence_entries(context):
        scope_keys = set(entry.get("scope_keys") or [])
        if (
            required_scope
            and not required_scope.issubset(scope_keys)
        ):
            continue
        reference = entry.get("reference")
        if reference:
            result.append(
                EvidenceReference.model_validate(reference)
            )
    return result


def _environment_evidence_covers(
    context: Any,
    scope_names: list[str],
) -> tuple[bool, list[str]]:
    required_scope = _normalized_scope(scope_names)
    required_kinds = {
        "curriculum_scope",
        "question_bank_aggregate",
    }
    covered_kinds: set[str] = set()

    for entry in _trusted_evidence_entries(context):
        kind = entry.get("kind")
        scope_keys = set(entry.get("scope_keys") or [])
        if (
            kind in required_kinds
            and required_scope
            and required_scope.issubset(scope_keys)
        ):
            covered_kinds.add(kind)

    missing = sorted(required_kinds - covered_kinds)
    return not missing, missing


def _dedupe_evidence(
    values: list[EvidenceReference],
) -> list[EvidenceReference]:
    result: list[EvidenceReference] = []
    seen: set[str] = set()
    for item in values:
        key = item.ref_id or (
            f"{item.kind}:{item.observed_by_run_id}:{item.summary}"
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def build_teaching_design_tools(context: Any) -> list[Any]:
    from ..tool_registry import AgentTool, EmptyInput, ExecutedTool

    def failed(code: str, message: str) -> Any:
        return ExecutedTool(
            payload={
                "ok": False,
                "code": code,
                "message": message,
            },
            status="failed",
            result_fields={
                "blocking_errors": [code],
            },
        )

    def recoverable(code: str, message: str) -> Any:
        """Protocol guard that lets the Agent correct itself in the same turn."""
        return ExecutedTool(
            payload={
                "ok": False,
                "code": code,
                "message": message,
            },
            status="completed",
            result_fields={},
        )

    def service() -> TeachingDesignService:
        return TeachingDesignService(context.session)

    def require_conversation() -> str | None:
        return context.conversation_id

    def read_active(_raw: BaseModel) -> Any:
        conversation_id = require_conversation()
        if not conversation_id:
            return failed(
                "teaching_design_requires_conversation",
                "教学设计需要明确的 conversation_id 才能维护当前版本。",
            )
        design = service().get_active(
            owner_key=context.owner_key,
            conversation_id=conversation_id,
        )
        if design is None:
            return failed(
                "no_active_teaching_design",
                "当前会话还没有正在讨论的教学设计。",
            )
        return ExecutedTool(
            payload={
                "ok": True,
                "teaching_design": design.model_dump(mode="json"),
            },
            status="completed",
            result_fields={
                "teaching_design": design,
            },
        )

    def create(raw: BaseModel) -> Any:
        conversation_id = require_conversation()
        if not conversation_id:
            return failed(
                "teaching_design_requires_conversation",
                "教学设计需要明确的 conversation_id 才能持久化。",
            )

        # The direct workflow entry keeps the public Tool schema unchanged.
        # Other callers retain the legacy Tool-loop implementation as fallback.
        if getattr(context, "use_teaching_design_workflow", False):
            values = CreateTeachingDesignInput.model_validate(raw)
            workflow_result = TeachingDesignWorkflow(
                context.session,
                workflow_trace=context.workflow_trace,
            ).execute(
                requirement=TeachingRequirement(
                    topic=context.user_message or values.content.title,
                    goal=values.content.objective,
                    content=values.content,
                ),
                owner_key=context.owner_key,
                conversation_id=conversation_id,
                run_id=context.run_id,
                source_user_message=context.user_message,
            )
            if workflow_result.ok:
                return ExecutedTool(
                    payload={
                        "ok": True,
                        "confirmation_required": True,
                        "teaching_design": workflow_result.teaching_design.model_dump(mode="json"),
                    },
                    status="waiting_confirmation",
                    result_fields={"teaching_design": workflow_result.teaching_design},
                )
            return recoverable(
                workflow_result.code or "teaching_design_workflow_failed",
                workflow_result.message or "教学设计创建流程未完成。",
            )

        td_service = service()
        active = td_service.get_active(
            owner_key=context.owner_key,
            conversation_id=conversation_id,
        )
        if active is not None and active.status in {
            "draft",
            "awaiting_confirmation",
        }:
            if active.source_user_message == context.user_message:
                return ExecutedTool(
                    payload={
                        "ok": True,
                        "confirmation_required": True,
                        "teaching_design": active.model_dump(mode="json"),
                    },
                    status="waiting_confirmation",
                    result_fields={"teaching_design": active},
                )
            return failed(
                "active_teaching_design_exists",
                "当前已有未确认教学设计；应修改现有版本，而不是静默创建另一份设计。",
            )

        values = CreateTeachingDesignInput.model_validate(raw)
        resolved_scope = context.inspection_state.get("validated_scope_names") or []
        if not resolved_scope:
            deterministic = resolve_deterministic_scope_labels(
                context.session,
                values.content.scope_names,
            )
            if not deterministic.ok:
                return recoverable(
                    "scope_resolution_required",
                    "请先确认教材章节或知识点范围，再创建教学设计。",
                )
            resolved_scope = deterministic.validated_scope_names
        values = values.model_copy(update={
            "content": values.content.model_copy(update={
                "scope_names": resolved_scope,
            })
        })

        covered, missing = _environment_evidence_covers(
            context,
            values.content.scope_names,
        )
        if not covered:
            return recoverable(
                "teaching_design_evidence_required",
                (
                    "创建新的 TeachingDesign 前必须先用当前运行中的真实环境 "
                    "Observation 覆盖同一教学范围。仍缺少："
                    + "、".join(missing)
                ),
            )

        trusted = _trusted_evidence_refs(
            context,
            scope_names=values.content.scope_names,
        )
        trusted_ids = {
            item.ref_id
            for item in trusted
            if item.ref_id
        }
        supplied_ids = {
            item.ref_id
            for item in values.content.evidence_refs
            if item.ref_id
        }
        if supplied_ids - trusted_ids:
            return recoverable(
                "untrusted_teaching_design_evidence",
                "evidence_refs 由系统根据当前 Tool Observation 管理，"
                "不能提交未观测到的证据引用。",
            )

        content = values.content.model_copy(
            update={
                "evidence_refs": trusted,
            }
        )

        design = td_service.create(
            owner_key=context.owner_key,
            conversation_id=conversation_id,
            content=content,
            run_id=context.run_id,
            source_user_message=context.user_message,
            change_reason="teacher_requested_new_teaching_design",
            ready_for_confirmation=True,
        )
        return ExecutedTool(
            payload={
                "ok": True,
                "confirmation_required": True,
                "teaching_design": design.model_dump(mode="json"),
            },
            status="waiting_confirmation",
            result_fields={
                "teaching_design": design,
            },
        )

    def revise(raw: BaseModel) -> Any:
        conversation_id = require_conversation()
        if not conversation_id:
            return failed(
                "teaching_design_requires_conversation",
                "教学设计需要明确的 conversation_id 才能修改。",
            )

        td_service = service()
        active = td_service.get_active(
            owner_key=context.owner_key,
            conversation_id=conversation_id,
        )
        if active is None:
            return failed(
                "no_active_teaching_design",
                "当前没有可修改的教学设计。",
            )

        values = ReviseTeachingDesignInput.model_validate(raw)

        if values.patch.evidence_refs is not None:
            return recoverable(
                "teaching_design_evidence_system_managed",
                "修改 TeachingDesign 时不要直接编辑 evidence_refs；"
                "系统会把本轮真实 Observation 自动附加到新版本。",
            )

        patch = values.patch
        scope_changed = (
            patch.scope_names is not None
            and _normalized_scope(patch.scope_names)
            != _normalized_scope(active.content.scope_names)
        )

        target_scope = (
            patch.scope_names
            if scope_changed
            else active.content.scope_names
        )
        trusted = _trusted_evidence_refs(
            context,
            scope_names=target_scope,
        )
        if scope_changed:
            covered, missing = _environment_evidence_covers(
                context,
                patch.scope_names or [],
            )
            if not covered:
                return recoverable(
                    "teaching_design_evidence_required",
                    (
                        "教学范围发生变化，必须先重新调查新范围。仍缺少："
                        + "、".join(missing)
                    ),
                )
            next_evidence = trusted
        elif trusted:
            next_evidence = _dedupe_evidence([
                *active.content.evidence_refs,
                *trusted,
            ])
        else:
            next_evidence = active.content.evidence_refs

        if trusted or scope_changed:
            patch = patch.model_copy(
                update={
                    "evidence_refs": next_evidence,
                }
            )

        try:
            design = td_service.revise(
                active.version_id,
                patch,
                conversation_id=conversation_id,
                run_id=context.run_id,
                source_user_message=context.user_message,
                change_reason=values.change_reason,
                ready_for_confirmation=True,
            )
        except StaleTeachingDesignError:
            return failed(
                "stale_teaching_design_version",
                "当前教学设计版本已经变化，请先读取最新版本再修改。",
            )
        except TeachingDesignStateError as exc:
            return failed(
                "teaching_design_state_error",
                str(exc),
            )

        return ExecutedTool(
            payload={
                "ok": True,
                "confirmation_required": True,
                "teaching_design": design.model_dump(mode="json"),
            },
            status="waiting_confirmation",
            result_fields={
                "teaching_design": design,
            },
        )

    def discard(_raw: BaseModel) -> Any:
        conversation_id = require_conversation()
        if not conversation_id:
            return failed(
                "teaching_design_requires_conversation",
                "教学设计需要明确的 conversation_id 才能放弃。",
            )
        active = service().get_active(
            owner_key=context.owner_key,
            conversation_id=conversation_id,
        )
        if active is None:
            return failed(
                "no_active_teaching_design",
                "当前没有可放弃的教学设计。",
            )
        try:
            design = service().discard_unconfirmed(
                active.version_id,
                conversation_id=conversation_id,
                run_id=context.run_id,
            )
        except (StaleTeachingDesignError, TeachingDesignStateError) as exc:
            return failed("teaching_design_discard_failed", str(exc))
        if context.state_store is not None:
            memory = context.state_store.get_memory(conversation_id)
            if memory.active_task.get("type") == "teaching_planning":
                memory.active_task = {
                    "type": "teaching_planning",
                    "status": "cancelled",
                    "waiting_for_scope": False,
                }
                context.state_store.set_memory(conversation_id, memory)
        return ExecutedTool(
            payload={
                "ok": True,
                "cancelled": True,
                "teaching_design": design.model_dump(mode="json"),
            },
            status="completed",
            result_fields={"teaching_design": design},
        )

    def confirm(_raw: BaseModel) -> Any:
        conversation_id = require_conversation()
        if not conversation_id:
            return failed(
                "teaching_design_requires_conversation",
                "教学设计需要明确的 conversation_id 才能确认。",
            )

        td_service = service()
        active = td_service.get_active(
            owner_key=context.owner_key,
            conversation_id=conversation_id,
        )
        if active is None:
            return failed(
                "no_active_teaching_design",
                "当前没有等待确认的教学设计。",
            )

        try:
            design = td_service.confirm(
                active.version_id,
                conversation_id=conversation_id,
                run_id=context.run_id,
            )
        except StaleTeachingDesignError:
            return failed(
                "stale_teaching_design_version",
                "当前教学设计版本已经变化，不能确认旧版本。",
            )
        except TeachingDesignStateError as exc:
            return failed(
                "teaching_design_state_error",
                str(exc),
            )

        generation = TeachingDesignPaperGenerationService(
            session=context.session,
            store=context.state_store,
            conversation_id=conversation_id,
        ).execute(design)

        if generation.ok and generation.paper is not None:
            context.paper_id = str(
                generation.paper.paper_id
            )
            context.version_id = str(
                generation.paper.version_id
            )

        status = (
            "completed"
            if generation.ok
            else "needs_clarification"
            if (
                generation.requires_design_revision
                or generation.clarification_questions
            )
            else "failed"
        )

        return ExecutedTool(
            payload={
                "ok": generation.ok,
                "confirmed": True,
                "teaching_design": design.model_dump(mode="json"),
                "paper_generation": generation.model_dump(mode="json"),
            },
            status=status,
            result_fields={
                "teaching_design": design,
                "teaching_design_generation": generation,
                "paper": generation.paper,
                "warnings": generation.warnings,
                "blocking_errors": generation.blocking_errors,
                "clarification_questions": generation.clarification_questions,
            },
        )

    def search_history(raw: BaseModel) -> Any:
        values = SearchTeachingDesignHistoryInput.model_validate(raw)
        candidates = service().recall_candidates(
            owner_key=context.owner_key,
            query=values.query,
            limit=10,
        )
        return ExecutedTool(
            payload={
                "ok": True,
                "candidates": [
                    item.model_dump(mode="json")
                    for item in candidates
                ],
            },
            status="completed",
            result_fields={
                "teaching_design_candidates": candidates,
            },
        )

    def activate(raw: BaseModel) -> Any:
        conversation_id = require_conversation()
        if not conversation_id:
            return failed(
                "teaching_design_requires_conversation",
                "恢复历史教学设计需要明确的 conversation_id。",
            )

        values = ActivateTeachingDesignInput.model_validate(raw)
        try:
            design = service().activate_historical_version(
                values.version_id,
                owner_key=context.owner_key,
                conversation_id=conversation_id,
                run_id=context.run_id,
            )
        except TeachingDesignNotFoundError:
            return failed(
                "teaching_design_not_found",
                "没有找到该教学设计版本，或它不属于当前教师。",
            )

        return ExecutedTool(
            payload={
                "ok": True,
                "activated": True,
                "teaching_design": design.model_dump(mode="json"),
            },
            status="completed",
            result_fields={
                "teaching_design": design,
            },
        )

    return [
        AgentTool(
            "read_active_teaching_design",
            "Read the persisted TeachingDesign currently active in this conversation. Read-only.",
            EmptyInput,
            read_active,
        ),
        AgentTool(
            "create_teaching_design",
            (
                "Create TeachingDesign v1 directly from the teacher's "
                "structured semantic requirement. For the deterministic "
                "TeachingDesign workflow entry, scope resolution, curriculum "
                "inspection, aggregate question-bank inspection, and evidence "
                "attachment execute inside this Tool; do not call those Tools "
                "first. evidence_refs are system-managed; do not invent them. "
                "The design becomes awaiting_confirmation and cannot be "
                "confirmed in the same teacher turn."
                if getattr(context, "use_teaching_design_workflow", False)
                else "Create TeachingDesign v1 only after current-run "
                "inspect_curriculum and aggregate inspect_question_bank "
                "observations cover the same scope. evidence_refs are "
                "system-managed from those observations; do not invent them. "
                "The design becomes awaiting_confirmation and cannot be "
                "confirmed in the same teacher turn."
            ),
            CreateTeachingDesignInput,
            create,
        ),
        AgentTool(
            "revise_teaching_design",
            (
                "Create a new immutable TeachingDesign version. Patch only "
                "teacher-changed semantic fields. evidence_refs are "
                "system-managed. A scope change requires fresh curriculum and "
                "aggregate question-bank inspection for the new scope."
            ),
            ReviseTeachingDesignInput,
            revise,
        ),
        AgentTool(
            "confirm_teaching_design",
            (
                "Confirm the active awaiting-confirmation TeachingDesign after "
                "explicit teacher acceptance, then immediately execute the "
                "deterministic paper-generation bridge. No second generation "
                "confirmation is allowed."
            ),
            EmptyInput,
            confirm,
        ),
        AgentTool(
            "discard_teaching_design",
            "Discard the active unconfirmed TeachingDesign, remove it from the active conversation state, and do not generate a Paper.",
            EmptyInput,
            discard,
        ),
        AgentTool(
            "search_teaching_design_history",
            "Search persisted TeachingDesign history for this teacher. Read-only.",
            SearchTeachingDesignHistoryInput,
            search_history,
        ),
        AgentTool(
            "activate_teaching_design",
            (
                "Make a retrieved historical TeachingDesign version active in "
                "this conversation without rewriting its historical status."
            ),
            ActivateTeachingDesignInput,
            activate,
        ),
    ]
