"""Thin Agent tools for deterministic teaching-environment inspection."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from calculus_agent.application.curriculum_retrieval import (
    retrieve_curriculum_candidates,
)
from calculus_agent.application.scope_resolution import (
    resolve_deterministic_scope_labels,
)
from calculus_agent.application.teaching_scope import (
    project_selectable_teaching_scopes,
)
from calculus_agent.application.teaching_environment import (
    InspectQuestionBankRequest,
    inspect_curriculum,
    inspect_question_bank,
)
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.agent.tool_registry import ExecutedTool


MAX_ENVIRONMENT_INSPECTION_CALLS = 4


def _validated_scope_for_inspection(context: Any, labels: list[str]) -> tuple[list[str] | None, ExecutedTool | None]:
    selected = context.inspection_state.get("validated_scope_names") or []
    if selected and set(_scope_keys(labels)).issubset(set(_scope_keys(selected))):
        return list(selected), None

    resolved = resolve_deterministic_scope_labels(context.session, labels)
    if resolved.ok:
        context.inspection_state["validated_scope_names"] = resolved.validated_scope_names
        return resolved.validated_scope_names, None

    message = "请先确认教材章节或知识点范围；我会先根据候选教材范围完成解析。"
    return None, ExecutedTool(
        payload={
            "ok": False,
            "code": "scope_resolution_required",
            "message": message,
            "unresolved_labels": resolved.unresolved_labels,
        },
        status="needs_clarification",
        result_fields={
            "blocking_errors": ["scope_resolution_required"],
            "clarification_questions": [message],
        },
    )


class RetrieveCurriculumCandidatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


class InspectCurriculumInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_names: list[str] = Field(min_length=1, max_length=20)


class InspectQuestionBankInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_names: list[str] = Field(min_length=1, max_length=20)
    detail_level: Literal["aggregate", "chapter_detail"] = "aggregate"
    chapter_name: str | None = Field(default=None, max_length=255)
    knowledge_names: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def detail_requires_chapter(self) -> "InspectQuestionBankInput":
        if self.detail_level == "chapter_detail" and not self.chapter_name:
            raise ValueError(
                "chapter_detail requires chapter_name"
            )
        if self.detail_level == "aggregate" and self.chapter_name:
            raise ValueError(
                "chapter_name is only valid for chapter_detail"
            )
        return self


def environment_inspection_tool_names() -> list[str]:
    return [
        "retrieve_curriculum_candidates",
        "inspect_curriculum",
        "inspect_question_bank",
    ]


def _scope_keys(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            normalize_name(value)
            for value in values
            if value and normalize_name(value)
        )
    )


def build_environment_inspection_tools(context: Any) -> list[Any]:
    from ..tool_registry import AgentTool, ExecutedTool

    def recoverable(
        code: str,
        message: str,
    ) -> ExecutedTool:
        return ExecutedTool(
            payload={
                "ok": False,
                "code": code,
                "message": message,
                "remaining_inspection_calls": max(
                    0,
                    MAX_ENVIRONMENT_INSPECTION_CALLS
                    - context.inspection_call_count,
                ),
            },
            status="completed",
            result_fields={
                "warnings": [code],
            },
        )

    def scope_failure(
        code: str,
        message: str,
        payload: dict,
    ) -> ExecutedTool:
        return ExecutedTool(
            payload={
                **payload,
                "ok": False,
                "code": code,
                "message": message,
                "remaining_inspection_calls": max(
                    0,
                    MAX_ENVIRONMENT_INSPECTION_CALLS
                    - context.inspection_call_count,
                ),
            },
            status="needs_clarification",
            result_fields={
                "blocking_errors": [code],
                "clarification_questions": [message],
            },
        )

    def consume_budget() -> bool:
        if (
            context.inspection_call_count
            >= MAX_ENVIRONMENT_INSPECTION_CALLS
        ):
            return False
        context.inspection_call_count += 1
        return True

    def register_evidence(
        *,
        reference,
        requested_scope_names: list[str],
        resolved_scope_names: list[str],
    ) -> None:
        if reference is None or not reference.ref_id:
            return
        context.observed_evidence[reference.ref_id] = {
            "reference": reference.model_dump(mode="json"),
            "kind": reference.kind,
            "scope_keys": _scope_keys([
                *requested_scope_names,
                *resolved_scope_names,
            ]),
        }

    def retrieve_curriculum_candidates_tool(raw: BaseModel) -> ExecutedTool:
        values = RetrieveCurriculumCandidatesInput.model_validate(raw)
        candidates = retrieve_curriculum_candidates(
            context.session,
            query=values.query,
            top_k=values.top_k,
        )
        projected = project_selectable_teaching_scopes(
            context.session,
            semantic_matches=candidates,
        )
        semantic_matches = [
            candidate.model_dump(mode="json")
            for candidate in projected.semantic_matches
        ]
        selectable_scopes = [
            candidate.model_dump(mode="json")
            for candidate in projected.selectable_scopes
        ]
        context.inspection_state["curriculum_semantic_matches"] = semantic_matches
        context.inspection_state["selectable_teaching_scopes"] = selectable_scopes
        if context.state_store is not None and context.conversation_id:
            memory = context.state_store.get_memory(context.conversation_id)
            current = (
                memory.active_task
                if memory.active_task.get("type") == "teaching_planning"
                else {}
            )
            memory.active_task = {
                **current,
                "type": "teaching_planning",
                "status": "scope_candidates_retrieved" if selectable_scopes else "awaiting_scope_clarification",
                "target_topic": values.query,
                "curriculum_semantic_matches": semantic_matches,
                "selectable_teaching_scopes": selectable_scopes,
                "waiting_for_scope": True,
            }
            context.state_store.set_memory(context.conversation_id, memory)
        payload = {
            "ok": bool(selectable_scopes),
            "query": values.query,
            "semantic_matches": semantic_matches,
            "selectable_scopes": selectable_scopes,
            "scope_selected": False,
        }
        if not selectable_scopes:
            message = "当前教材中没有找到可确认的教学范围，请补充章节、小节或知识点名称。"
            return ExecutedTool(
                payload={
                    **payload,
                    "code": "curriculum_candidates_not_found",
                    "message": message,
                },
                status="needs_clarification",
                result_fields={
                    "blocking_errors": ["curriculum_candidates_not_found"],
                    "clarification_questions": [message],
                },
            )
        return ExecutedTool(
            payload=payload,
            status="completed",
            result_fields={},
        )

    def inspect_curriculum_tool(raw: BaseModel) -> ExecutedTool:
        values = InspectCurriculumInput.model_validate(raw)

        if not consume_budget():
            return recoverable(
                "environment_inspection_budget_exhausted",
                "本轮环境调查已达到4次上限；"
                "请基于已有 Observation 形成设计，"
                "或向教师说明仍缺少的关键证据。",
            )

        validated_scope, resolution_error = _validated_scope_for_inspection(
            context,
            values.scope_names,
        )
        if resolution_error is not None:
            return resolution_error
        result = inspect_curriculum(
            context.session,
            scope_names=validated_scope or [],
            run_id=context.run_id,
        )
        payload = result.model_dump(mode="json")
        payload["remaining_inspection_calls"] = max(
            0,
            MAX_ENVIRONMENT_INSPECTION_CALLS
            - context.inspection_call_count,
        )

        if not result.ok:
            return scope_failure(
                "curriculum_scope_unresolved",
                "部分教学范围无法映射到当前激活教材目录，"
                "请确认章节名称。",
                payload,
            )

        register_evidence(
            reference=result.evidence_ref,
            requested_scope_names=(
                result.requested_scope_names
            ),
            resolved_scope_names=(
                result.resolved_scope_names
            ),
        )
        context.inspection_state[
            "curriculum_scope_keys"
        ] = _scope_keys([
            *result.requested_scope_names,
            *result.resolved_scope_names,
        ])

        return ExecutedTool(
            payload=payload,
            status="completed",
            result_fields={},
        )

    def inspect_question_bank_tool(raw: BaseModel) -> ExecutedTool:
        values = InspectQuestionBankInput.model_validate(raw)

        if not consume_budget():
            return recoverable(
                "environment_inspection_budget_exhausted",
                "本轮环境调查已达到4次上限；"
                "请基于已有 Observation 形成设计，"
                "不要继续无目的查询题库。",
            )

        validated_scope, resolution_error = _validated_scope_for_inspection(
            context,
            values.scope_names,
        )
        if resolution_error is not None:
            return resolution_error
        requested_scope_keys = set(_scope_keys(validated_scope or []))

        if values.detail_level == "chapter_detail":
            aggregate_scope_keys = set(
                context.inspection_state.get(
                    "question_bank_aggregate_scope_keys",
                    [],
                )
            )
            if (
                not aggregate_scope_keys
                or not requested_scope_keys.issubset(
                    aggregate_scope_keys
                )
            ):
                return recoverable(
                    "question_bank_aggregate_required",
                    "题库 drill-down 必须先对同一范围执行 "
                    "aggregate 调查；不要跳过总览直接扫描细节。",
                )

        result = inspect_question_bank(
            context.session,
            InspectQuestionBankRequest(
                scope_names=validated_scope or [],
                detail_level=values.detail_level,
                chapter_name=values.chapter_name,
                knowledge_names=values.knowledge_names,
            ),
            run_id=context.run_id,
        )
        payload = result.model_dump(mode="json")
        payload["remaining_inspection_calls"] = max(
            0,
            MAX_ENVIRONMENT_INSPECTION_CALLS
            - context.inspection_call_count,
        )

        if not result.ok:
            return scope_failure(
                "question_bank_scope_unresolved",
                "题库调查范围无法唯一映射到当前教材章节，"
                "请先确认范围。",
                payload,
            )

        register_evidence(
            reference=result.evidence_ref,
            requested_scope_names=(
                result.requested_scope_names
            ),
            resolved_scope_names=(
                result.resolved_scope_names
            ),
        )

        if values.detail_level == "aggregate":
            context.inspection_state[
                "question_bank_aggregate_scope_keys"
            ] = _scope_keys([
                *result.requested_scope_names,
                *result.resolved_scope_names,
            ])

        return ExecutedTool(
            payload=payload,
            status="completed",
            result_fields={},
        )

    return [
        AgentTool(
            "retrieve_curriculum_candidates",
            (
                "Retrieve read-only CurriculumNode and KnowledgeNode candidates "
                "for a teacher's natural-language topic. This is candidate recall "
                "only: it does not select, validate, or modify teaching scope."
            ),
            RetrieveCurriculumCandidatesInput,
            retrieve_curriculum_candidates_tool,
        ),
        AgentTool(
            "inspect_curriculum",
            (
                "Read-only aggregate inspection of the active curriculum for "
                "a requested teaching scope. Use this before creating a new "
                "TeachingDesign so scope and available controlled knowledge "
                "are grounded in current DB state. It returns compact evidence "
                "for the current Agent run and never changes curriculum data."
            ),
            InspectCurriculumInput,
            inspect_curriculum_tool,
        ),
        AgentTool(
            "inspect_question_bank",
            (
                "Read-only inspection of teacher-facing paper candidate supply "
                "using the exact same base eligibility rules as generation. "
                "Start with detail_level=aggregate. Use chapter_detail only "
                "after aggregate when supply imbalance or a specific knowledge "
                "decision genuinely needs more evidence. At most 4 total "
                "environment-inspection calls are allowed per Agent turn."
            ),
            InspectQuestionBankInput,
            inspect_question_bank_tool,
        ),
    ]
