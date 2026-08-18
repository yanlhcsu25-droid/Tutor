"""Thin Agent tools for deterministic teaching-environment inspection."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from calculus_agent.application.teaching_environment import (
    InspectQuestionBankRequest,
    inspect_curriculum,
    inspect_question_bank,
)
from calculus_agent.knowledge.normalization import normalize_name


MAX_ENVIRONMENT_INSPECTION_CALLS = 4


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

    def inspect_curriculum_tool(raw: BaseModel) -> ExecutedTool:
        values = InspectCurriculumInput.model_validate(raw)

        if not consume_budget():
            return recoverable(
                "environment_inspection_budget_exhausted",
                "本轮环境调查已达到4次上限；"
                "请基于已有 Observation 形成设计，"
                "或向教师说明仍缺少的关键证据。",
            )

        result = inspect_curriculum(
            context.session,
            scope_names=values.scope_names,
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

        requested_scope_keys = set(
            _scope_keys(values.scope_names)
        )

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
                scope_names=values.scope_names,
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
