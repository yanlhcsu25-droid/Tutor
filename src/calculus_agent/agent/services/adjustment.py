"""Adjustment application service for Teacher Agent use cases.

Phase 4B-3 extracts adjustment orchestration from ``tool_registry.py`` while
leaving the deterministic AdjustmentPlan algorithms in ``tools.analysis_tools``.
The service owns teacher-facing delete-address resolution, pending AdjustmentPlan
tracking, later total-score patch merge, and confirm lifecycle.

It deliberately does not know about Agent Tool wrappers, model calls, or HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from calculus_agent.models import AdjustmentPlanRecord
from calculus_agent.papers.addressing import QuestionAddress, resolve_section_item

from ..tools.analysis_tools import (
    ConfirmAdjustmentResult,
    KnowledgePreference,
    PaperAdjustmentPreview,
    confirm_adjust_paper,
    preview_adjust_paper,
)


class AdjustmentStateStore(Protocol):
    def get_adjustment(self, conversation_id: str) -> str | None: ...
    def set_adjustment(self, conversation_id: str, plan_id: str) -> None: ...
    def clear_adjustment(self, conversation_id: str) -> None: ...


class AdjustmentServiceError(RuntimeError):
    """Expected application-service failure with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class AdjustmentService:
    session: Session
    store: AdjustmentStateStore | None
    conversation_id: str | None

    def pending_plan_id(self) -> str | None:
        if self.store is None or not self.conversation_id:
            return None
        return self.store.get_adjustment(self.conversation_id)

    def has_pending(self) -> bool:
        return self.pending_plan_id() is not None

    def track_plan(self, plan_id: str) -> None:
        if self.store is None or not self.conversation_id:
            return
        self.store.set_adjustment(self.conversation_id, plan_id)

    def clear_pending(self) -> None:
        if self.store is None or not self.conversation_id:
            return
        self.store.clear_adjustment(self.conversation_id)

    def preview(
        self,
        *,
        paper_id: str | None,
        knowledge_preferences: list[KnowledgePreference],
        question_type_changes: dict[str, int],
        remove_addresses: list[QuestionAddress],
        remove_positions: list[int],
        target_total_score: float | None,
    ) -> PaperAdjustmentPreview:
        if not paper_id:
            raise AdjustmentServiceError(
                "no_current_paper",
                "当前还没有可调整的试卷。",
            )

        resolved_remove_positions = list(remove_positions)

        if remove_addresses:
            resolved_remove_positions = []
            for address in remove_addresses:
                item = resolve_section_item(
                    self.session,
                    paper_id=paper_id,
                    section_type=address.section_type,
                    section_order=address.section_order,
                )
                if item is None:
                    raise AdjustmentServiceError(
                        "question_address_not_found",
                        (
                            f"当前试卷没有{address.section_type}"
                            f"第{address.section_order}题。"
                        ),
                    )
                resolved_remove_positions.append(item.position)

        # Preserve existing deterministic pending-patch behavior:
        # deletion preview first, then a later explicit total-score patch
        # reuses the pending removal positions in the new preview.
        pending_plan_id = self.pending_plan_id()
        if (
            pending_plan_id
            and not resolved_remove_positions
            and not remove_addresses
            and target_total_score is not None
        ):
            pending_plan = self.session.get(
                AdjustmentPlanRecord,
                pending_plan_id,
            )
            if pending_plan is not None:
                resolved_remove_positions = [
                    operation["position"]
                    for operation in pending_plan.operations_json
                    if operation.get("type") == "remove_question"
                ]

        result = preview_adjust_paper(
            self.session,
            paper_id=paper_id,
            knowledge_preferences=knowledge_preferences,
            question_type_changes=question_type_changes,
            remove_positions=resolved_remove_positions,
            target_total_score=target_total_score,
        )

        if result.ok and result.plan:
            self.track_plan(result.plan.plan_id)

        return result

    def confirm(
        self,
        *,
        paper_id: str | None,
        version_id: str | None,
    ) -> ConfirmAdjustmentResult:
        plan_id = self.pending_plan_id()
        if not plan_id:
            raise AdjustmentServiceError(
                "no_pending_adjustment",
                "当前没有等待确认的整卷调整方案。",
            )

        if not paper_id or not version_id:
            raise AdjustmentServiceError(
                "no_current_paper",
                "当前还没有可调整的试卷。",
            )

        result = confirm_adjust_paper(
            self.session,
            plan_id=plan_id,
            paper_id=paper_id,
            current_version_id=version_id,
        )
        if result.ok:
            self.clear_pending()
        return result
