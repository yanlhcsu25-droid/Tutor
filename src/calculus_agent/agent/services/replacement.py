"""Replacement application service shared by Teacher Agent adapters.

Phase 4B-2 extracts the single-question replacement use case from
``tool_registry.py`` without changing business behavior.  The service owns
teacher-facing address resolution, deterministic dry-run orchestration,
PendingReplacement persistence, and confirm/cancel lifecycle.  It does not
know about model calls, Agent Tool wrappers, or HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import PaperItem, QuestionKnowledgeLink
from calculus_agent.papers.addressing import QuestionAddress, resolve_section_item

from ..conversation_state import PendingReplacement
from ..schemas import ReplacementIntent
from ..tools.replacement_tools import (
    ApplyReplacementResult,
    ReplacementDryRunResult,
    apply_question_replacement,
    dry_run_replace_question,
)


class ReplacementStateStore(Protocol):
    def get(self, conversation_id: str) -> PendingReplacement | None: ...
    def set(
        self,
        conversation_id: str,
        action: PendingReplacement,
    ) -> None: ...
    def clear(self, conversation_id: str) -> None: ...


class ReplacementServiceError(RuntimeError):
    """Expected application-service failure with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReplacementPreviewOutcome:
    result: ReplacementDryRunResult
    pending_action: PendingReplacement | None = None


@dataclass
class ReplacementService:
    session: Session
    store: ReplacementStateStore | None
    conversation_id: str | None

    def _pending(self) -> PendingReplacement | None:
        if self.store is None or not self.conversation_id:
            return None
        return self.store.get(self.conversation_id)

    def preview(
        self,
        *,
        paper_id: str | None,
        version_id: str | None,
        address: QuestionAddress | None,
        position: int | None,
        difficulty_direction: Literal["easier", "harder", "same"] | None,
        target_difficulty: int | None,
        preserve_knowledge_points: bool,
        avoid_similarity_with_question_numbers: list[int],
    ) -> ReplacementPreviewOutcome:
        if not paper_id or not version_id:
            raise ReplacementServiceError(
                "no_current_paper",
                "当前还没有可修改的试卷。",
            )

        if self._pending() is not None:
            raise ReplacementServiceError(
                "pending_replacement_exists",
                "当前已有待确认换题方案；必须先确认或取消，不能用新预览静默覆盖。",
            )

        if address is not None:
            target_item = resolve_section_item(
                self.session,
                paper_id=version_id,
                section_type=address.section_type,
                section_order=address.section_order,
            )
            if target_item is None:
                raise ReplacementServiceError(
                    "question_address_not_found",
                    f"当前试卷没有{address.section_type}第{address.section_order}题。",
                )
            target_position = target_item.position
        else:
            target_position = position

        if target_position is None:
            raise ReplacementServiceError(
                "invalid_replacement_target",
                "没有可解析的换题目标。",
            )

        target_knowledge: list[str] = []
        if preserve_knowledge_points:
            item = self.session.scalar(
                select(PaperItem).where(
                    PaperItem.paper_id == version_id,
                    PaperItem.position == target_position,
                )
            )
            if item is not None:
                target_knowledge = sorted(
                    set(
                        self.session.scalars(
                            select(
                                QuestionKnowledgeLink.knowledge_node_id
                            ).where(
                                QuestionKnowledgeLink.question_id
                                == item.question_id
                            )
                        )
                    )
                )
                if not target_knowledge:
                    raise ReplacementServiceError(
                        "current_question_knowledge_missing",
                        "当前题目没有可验证的知识点标注，无法保证换题后知识点不变。",
                    )

        intent = ReplacementIntent(
            target_position=target_position,
            difficulty_direction=difficulty_direction or "same",
            target_difficulty=target_difficulty,
            target_knowledge_node_ids=target_knowledge,
            avoid_similarity_with_question_numbers=(
                avoid_similarity_with_question_numbers
            ),
        )
        result = dry_run_replace_question(
            self.session,
            paper_id=paper_id,
            version_id=version_id,
            intent=intent,
        )
        if not result.ok:
            return ReplacementPreviewOutcome(result=result)

        if self.store is None or not self.conversation_id:
            raise ReplacementServiceError(
                "missing_conversation_context",
                "无法保存待确认的换题方案。",
            )

        pending = PendingReplacement(
            paper_id=paper_id,
            source_version_id=version_id,
            target_position=target_position,
            old_question_id=result.current_question.question_id,
            replacement_question_id=(
                result.recommended_question.question_id
            ),
            difficulty_direction=intent.difficulty_direction,
            target_difficulty=intent.target_difficulty,
            preserve_knowledge_points=preserve_knowledge_points,
            required_knowledge_node_ids=target_knowledge,
            warnings=result.warnings,
        )
        self.store.set(self.conversation_id, pending)
        return ReplacementPreviewOutcome(
            result=result,
            pending_action=pending,
        )

    def confirm(self) -> ApplyReplacementResult:
        pending = self._pending()
        if pending is None:
            raise ReplacementServiceError(
                "no_pending_action",
                "当前没有等待确认的单题替换方案。",
            )

        result = apply_question_replacement(
            self.session,
            paper_id=pending.paper_id,
            source_version_id=pending.source_version_id,
            target_position=pending.target_position,
            replacement_question_id=pending.replacement_question_id,
            difficulty_direction=pending.difficulty_direction,
            target_difficulty=pending.target_difficulty,
            preserve_knowledge_points=pending.preserve_knowledge_points,
            required_knowledge_node_ids=pending.required_knowledge_node_ids,
        )
        if result.ok and self.store is not None and self.conversation_id:
            self.store.clear(self.conversation_id)
        return result

    def cancel(self) -> PendingReplacement:
        pending = self._pending()
        if pending is None:
            raise ReplacementServiceError(
                "no_pending_action",
                "当前没有等待取消的单题替换方案。",
            )
        if self.store is not None and self.conversation_id:
            self.store.clear(self.conversation_id)
        return pending
