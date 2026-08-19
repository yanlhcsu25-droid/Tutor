"""Unified deterministic preview service for existing-paper changes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from calculus_agent.models import AdjustmentPlanRecord, Paper, PaperItem, Question, QuestionDraft
from calculus_agent.papers.addressing import QuestionAddress, resolve_section_item
from calculus_agent.question_types import PAPER_QUESTION_TYPES, canonical_question_type
from calculus_agent.questions.eligibility import EXCLUDED_PAPER_SOURCE_NAMES
from .tools.analysis_tools import (
    AdjustmentOperation,
    AdjustmentPlan,
    PaperSummary,
    _knowledge,
    _profiles,
    _question_in_version_scope,
    _summary,
    analyze_paper,
    validate_adjustment_operations,
)

QuestionType = Literal["选择题", "多选题", "填空题", "计算题", "证明题"]


class ReplaceQuestionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["replace_question"]
    target: QuestionAddress
    difficulty_direction: Literal["easier", "harder", "same"] | None = None
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    preserve_knowledge_points: bool = False
    avoid_similarity_with: list[QuestionAddress] = Field(default_factory=list)


class RemoveQuestionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["remove_question"]
    target: QuestionAddress


class AddQuestionsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["add_questions"]
    question_type: QuestionType
    count: int = Field(default=1, ge=1, le=20)
    score: float | None = Field(default=None, gt=0, le=300)


class ChangeQuestionScoreChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["change_question_score"]
    target: QuestionAddress
    score: float = Field(gt=0, le=300)


class ChangeQuestionTypeDistributionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["change_question_type_distribution"]
    changes: dict[str, int]

    @model_validator(mode="after")
    def non_empty_changes(self) -> "ChangeQuestionTypeDistributionChange":
        if not self.changes or all(value == 0 for value in self.changes.values()):
            raise ValueError("changes 至少包含一个非零题型变化")
        return self


PaperChangeOperation = Annotated[
    ReplaceQuestionChange
    | RemoveQuestionChange
    | AddQuestionsChange
    | ChangeQuestionScoreChange
    | ChangeQuestionTypeDistributionChange,
    Field(discriminator="type"),
]


class PaperChangeRequest(BaseModel):
    """Only teacher-requested change intent; runtime IDs are intentionally absent."""
    model_config = ConfigDict(extra="forbid")
    operations: list[PaperChangeOperation] = Field(default_factory=list, max_length=50)
    target_total_score: float | None = Field(default=None, gt=0, le=1000)

    @model_validator(mode="after")
    def has_effect(self) -> "PaperChangeRequest":
        if not self.operations and self.target_total_score is None:
            raise ValueError("至少需要一个试卷修改操作或 target_total_score")
        return self


class PaperChangePreview(BaseModel):
    ok: bool
    paper_id: str
    plan: AdjustmentPlan | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)


class PaperChangeServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _PlannedEntry:
    question_id: str
    section: str
    score: float
    source_position: int | None = None
    operation: AdjustmentOperation | None = None
    score_locked: bool = False


@dataclass
class PaperChangeService:
    """Compile teacher-facing PaperChangeRequest into one AdjustmentPlan.

    This service does not mutate Paper state. It resolves addresses, selects
    deterministic candidates, validates the whole operation set, persists one
    preview plan, and moves only the pending-plan pointer.
    """

    session: Any
    store: Any
    conversation_id: str | None
    paper_id: str | None
    version_id: str | None

    def _current_version_id(self) -> str:
        value = self.version_id or self.paper_id
        if not value:
            raise PaperChangeServiceError(
                "no_current_paper",
                "当前还没有可修改的试卷。",
            )
        return value

    def _root_paper_id(self, version: Paper) -> str:
        return version.root_paper_id or version.id

    def _resolve_position(self, version_id: str, address) -> int:
        item = resolve_section_item(
            self.session,
            paper_id=version_id,
            section_type=address.section_type,
            section_order=address.section_order,
        )
        if item is None:
            raise PaperChangeServiceError(
                "question_address_not_found",
                f"当前试卷没有{address.section_type}第{address.section_order}题。",
            )
        return item.position

    def _legacy_pending_guard(self) -> None:
        if not self.store or not self.conversation_id:
            return
        if self.store.get(self.conversation_id) is not None:
            raise PaperChangeServiceError(
                "legacy_pending_replacement_exists",
                "当前还有旧版待确认换题方案；请先放弃该未提交方案，再创建新的试卷修改计划。",
            )
        if (
            hasattr(self.store, "get_generation")
            and self.store.get_generation(self.conversation_id) is not None
        ):
            raise PaperChangeServiceError(
                "pending_generation_exists",
                "当前有待确认组卷方案；请先确认或放弃该方案，再修改已有试卷。",
            )

    def _load_pending_operations(
        self,
        version_id: str,
    ) -> tuple[list[AdjustmentOperation], dict[int, dict[str, Any]]]:
        if not self.store or not self.conversation_id or not hasattr(self.store, "get_adjustment"):
            return [], {}
        plan_id = self.store.get_adjustment(self.conversation_id)
        if not plan_id:
            return [], {}
        record = self.session.get(AdjustmentPlanRecord, plan_id)
        if record is None:
            self.store.clear_adjustment(self.conversation_id)
            return [], {}
        if record.base_paper_version_id != version_id or record.status != "pending":
            self.store.clear_adjustment(self.conversation_id)
            return [], {}
        contracts: dict[int, dict[str, Any]] = {}
        operations: list[AdjustmentOperation] = []
        for value in record.operations_json:
            operation = AdjustmentOperation.model_validate(value)
            operations.append(operation)
            contract = value.get("_confirmation_contract") if isinstance(value, dict) else None
            if operation.type == "replace_question" and isinstance(contract, dict):
                contracts[operation.position] = dict(contract)
        return operations, contracts

    def _all_candidates(self, profiles: dict[str, int]) -> list[Question]:
        return list(
            self.session.scalars(
                select(Question)
                .join(QuestionDraft, QuestionDraft.id == Question.draft_id)
                .where(
                    Question.review_status == "approved",
                    Question.is_active.is_(True),
                    Question.knowledge_match_status == "current",
                    Question.id.in_(profiles),
                    QuestionDraft.source_name.not_in(EXCLUDED_PAPER_SOURCE_NAMES),
                )
            ).all()
        )

    def _select_replacement(
        self,
        *,
        version: Paper,
        items_by_position: dict[int, PaperItem],
        position: int,
        change: ReplaceQuestionChange,
        candidates: list[Question],
        profiles: dict[str, int],
        knowledge: dict[str, set[str]],
        occupied: set[str],
    ) -> tuple[Question | None, str | None, list[str]]:
        target = items_by_position[position]
        current_difficulty = profiles.get(target.question_id)
        if current_difficulty is None:
            return None, "current_question_difficulty_missing", []

        target_knowledge = knowledge[target.question_id]
        required_knowledge = target_knowledge if change.preserve_knowledge_points else set()

        avoid_knowledge: set[str] = set()
        for address in change.avoid_similarity_with:
            ref_position = self._resolve_position(version.id, address)
            ref = items_by_position.get(ref_position)
            if ref is not None:
                avoid_knowledge.update(knowledge[ref.question_id])

        matches = [
            question
            for question in candidates
            if question.id not in occupied
            and canonical_question_type(question.question_type)
            == canonical_question_type(target.section)
            and _question_in_version_scope(
                self.session,
                version,
                question,
                knowledge[question.id],
            )
        ]
        if required_knowledge:
            matches = [
                question
                for question in matches
                if required_knowledge.issubset(knowledge[question.id])
            ]
        if avoid_knowledge:
            matches = [
                question
                for question in matches
                if not knowledge[question.id].intersection(avoid_knowledge)
            ]

        if change.target_difficulty is not None:
            if change.target_difficulty == current_difficulty:
                return None, "target_difficulty_same_as_current", []
            matches = [
                question
                for question in matches
                if profiles[question.id] == change.target_difficulty
            ]
            unavailable = "target_difficulty_candidate_not_found"
            key = lambda question: (
                -len(knowledge[question.id].intersection(target_knowledge)),
                question.id,
            )
        else:
            direction = change.difficulty_direction or "same"
            if direction == "easier":
                matches = [
                    question
                    for question in matches
                    if profiles[question.id] < current_difficulty
                ]
                unavailable = "no_easier_candidate"
                key = lambda question: (
                    current_difficulty - profiles[question.id],
                    -len(knowledge[question.id].intersection(target_knowledge)),
                    question.id,
                )
            elif direction == "harder":
                matches = [
                    question
                    for question in matches
                    if profiles[question.id] > current_difficulty
                ]
                unavailable = "no_harder_candidate"
                key = lambda question: (
                    profiles[question.id] - current_difficulty,
                    -len(knowledge[question.id].intersection(target_knowledge)),
                    question.id,
                )
            else:
                matches = [
                    question
                    for question in matches
                    if profiles[question.id] == current_difficulty
                ]
                unavailable = "same_difficulty_candidate_not_found"
                key = lambda question: (
                    -len(knowledge[question.id].intersection(target_knowledge)),
                    question.id,
                )

        if not matches:
            return None, unavailable, []
        matches.sort(key=key)
        selected = matches[0]
        warnings: list[str] = []
        if (
            not change.preserve_knowledge_points
            and not knowledge[selected.id].intersection(target_knowledge)
        ):
            warnings.append("knowledge_point_constraint_relaxed")
        return selected, None, warnings

    def _infer_add_score(
        self,
        items: list[PaperItem],
        *,
        question_type: str,
        explicit_score: float | None,
    ) -> tuple[float | None, str | None, list[str]]:
        if explicit_score is not None:
            return float(explicit_score), None, []
        canonical = canonical_question_type(question_type)
        scores = {
            float(item.score)
            for item in items
            if canonical_question_type(item.section) == canonical
        }
        if not scores:
            return (
                None,
                "add_question_score_required",
                ["当前试卷没有该题型，请明确新增题目的分值。"],
            )
        if len(scores) > 1:
            return (
                None,
                "add_question_score_ambiguous",
                ["当前该题型存在多种分值，请明确新增题目的分值。"],
            )
        return next(iter(scores)), None, []

    def _section_insert_position(self, items: list[PaperItem], question_type: str) -> int:
        canonical = canonical_question_type(question_type)
        if canonical not in PAPER_QUESTION_TYPES:
            raise PaperChangeServiceError("question_type_invalid", "题型无效。")
        same = [
            item
            for item in items
            if canonical_question_type(item.section) == canonical
        ]
        if same:
            return max(item.position for item in same) + 1
        order = list(PAPER_QUESTION_TYPES)
        target_index = order.index(canonical)
        for item in sorted(items, key=lambda value: value.position):
            item_type = canonical_question_type(item.section)
            if item_type in order and order.index(item_type) > target_index:
                return item.position
        return len(items) + 1

    def _merge_source_operation(
        self,
        operations: list[AdjustmentOperation],
        incoming: AdjustmentOperation,
        *,
        score_locked: bool = False,
        score_locks: set[int],
    ) -> None:
        if incoming.type == "add_question":
            operations.append(incoming)
            return

        previous = next(
            (
                operation
                for operation in operations
                if operation.type != "add_question"
                and operation.position == incoming.position
            ),
            None,
        )
        if previous is None:
            operations.append(incoming)
            if score_locked:
                score_locks.add(incoming.position)
            return

        if incoming.type == "remove_question":
            operations.remove(previous)
            operations.append(incoming)
            score_locks.discard(incoming.position)
            return
        if previous.type == "remove_question":
            raise PaperChangeServiceError(
                "paper_change_target_conflict",
                f"第 {incoming.position} 个内部位置已计划删除，不能同时执行其他修改。",
            )
        if incoming.type == "change_score":
            if previous.type == "replace_question":
                previous.score_after = incoming.score_after
            else:
                operations.remove(previous)
                operations.append(incoming)
            if score_locked:
                score_locks.add(incoming.position)
            return
        if incoming.type == "replace_question":
            if previous.type == "change_score":
                incoming.score_after = previous.score_after
            operations.remove(previous)
            operations.append(incoming)
            return

        raise PaperChangeServiceError(
            "paper_change_target_conflict",
            "同一道题存在无法合并的修改操作。",
        )

    def _planned_entries(
        self,
        items: list[PaperItem],
        operations: list[AdjustmentOperation],
        *,
        question_types: dict[str, str],
        score_locks: set[int],
        locked_add_ids: set[int],
    ) -> list[_PlannedEntry]:
        mutations = {
            operation.position: operation
            for operation in operations
            if operation.type != "add_question"
        }
        additions: dict[int, list[AdjustmentOperation]] = defaultdict(list)
        for operation in operations:
            if operation.type == "add_question":
                additions[operation.position].append(operation)

        result: list[_PlannedEntry] = []
        by_position = {item.position: item for item in items}
        for source_position in range(1, len(items) + 2):
            for operation in additions.get(source_position, []):
                if not operation.new_question_id:
                    continue
                result.append(
                    _PlannedEntry(
                        question_id=operation.new_question_id,
                        section=canonical_question_type(
                            operation.section
                            or question_types.get(operation.new_question_id, "")
                        ),
                        score=float(operation.score_after),
                        operation=operation,
                        score_locked=id(operation) in locked_add_ids,
                    )
                )

            source = by_position.get(source_position)
            if source is None:
                continue
            operation = mutations.get(source_position)
            if operation and operation.type == "remove_question":
                continue
            if operation and operation.type == "replace_question":
                question_id = operation.new_question_id or source.question_id
                section = canonical_question_type(
                    question_types.get(question_id, source.section)
                )
                score = float(operation.score_after)
            elif operation and operation.type == "change_score":
                question_id = source.question_id
                section = canonical_question_type(source.section)
                score = float(operation.score_after)
            else:
                question_id = source.question_id
                section = canonical_question_type(source.section)
                score = float(source.score)
            result.append(
                _PlannedEntry(
                    question_id=question_id,
                    section=section,
                    score=score,
                    source_position=source_position,
                    operation=operation,
                    score_locked=source_position in score_locks,
                )
            )
        return result

    def _rebalance_to_total(
        self,
        *,
        items: list[PaperItem],
        operations: list[AdjustmentOperation],
        question_types: dict[str, str],
        target_total_score: float,
        score_locks: set[int],
        locked_add_ids: set[int],
    ) -> tuple[list[_PlannedEntry], str | None]:
        entries = self._planned_entries(
            items,
            operations,
            question_types=question_types,
            score_locks=score_locks,
            locked_add_ids=locked_add_ids,
        )
        difference = target_total_score - sum(entry.score for entry in entries)
        if abs(difference) < 1e-9:
            return entries, None

        preferred = ["计算题", "证明题", "填空题", "选择题", "多选题"]
        groups: dict[str, list[_PlannedEntry]] = defaultdict(list)
        for entry in entries:
            if not entry.score_locked:
                groups[entry.section].append(entry)
        ordered_types = sorted(
            groups,
            key=lambda name: preferred.index(name) if name in preferred else len(preferred),
        )

        for question_type in ordered_types:
            group = groups[question_type]
            if not group:
                continue
            delta_each = difference / len(group)
            new_scores = [entry.score + delta_each for entry in group]
            if not all(
                score > 0 and abs(score * 2 - round(score * 2)) < 1e-9
                for score in new_scores
            ):
                continue
            for entry, new_score in zip(group, new_scores):
                entry.score = float(new_score)
                if entry.operation is not None:
                    entry.operation.score_after = float(new_score)
                elif entry.source_position is not None:
                    source = next(
                        item for item in items if item.position == entry.source_position
                    )
                    operation = AdjustmentOperation(
                        type="change_score",
                        position=entry.source_position,
                        old_question_id=source.question_id,
                        score_before=float(source.score),
                        score_after=float(new_score),
                    )
                    operations.append(operation)
                    entry.operation = operation
            return entries, None
        return entries, "score_rebalance_ambiguous"

    def _summary_from_entries(
        self,
        entries: list[_PlannedEntry],
        *,
        profiles: dict[str, int],
        knowledge: dict[str, set[str]],
        names: dict[str, str],
        paper_id: str,
    ) -> PaperSummary:
        virtual = [
            PaperItem(
                paper_id=paper_id,
                question_id=entry.question_id,
                section=entry.section,
                position=index,
                score=entry.score,
                locked=False,
            )
            for index, entry in enumerate(entries, 1)
        ]
        return _summary(virtual, profiles, knowledge, names)

    def validate_confirmation_contracts(self, plan_id: str) -> list[str]:
        """Revalidate preview-time hard replacement contracts before mutation."""
        record = self.session.get(AdjustmentPlanRecord, plan_id)
        if record is None:
            return ["adjustment_plan_not_found"]
        version_id = self._current_version_id()
        if record.base_paper_version_id != version_id:
            return ["stale_adjustment_plan"]
        items = {
            item.position: item
            for item in self.session.scalars(
                select(PaperItem).where(PaperItem.paper_id == version_id)
            ).all()
        }
        profiles = _profiles(self.session)
        knowledge, _names = _knowledge(self.session)
        errors: list[str] = []
        for value in record.operations_json:
            if not isinstance(value, dict) or value.get("type") != "replace_question":
                continue
            contract = value.get("_confirmation_contract")
            if not isinstance(contract, dict):
                continue
            position = int(value.get("position") or 0)
            source = items.get(position)
            replacement_id = value.get("new_question_id")
            replacement = self.session.get(Question, replacement_id) if replacement_id else None
            if source is None or replacement is None:
                errors.append("replacement_confirmation_contract_invalid")
                continue

            if contract.get("preserve_knowledge_points"):
                required = set(contract.get("required_knowledge_node_ids") or [])
                if (
                    not required
                    or not required.issubset(knowledge[source.question_id])
                    or not required.issubset(knowledge[replacement.id])
                ):
                    errors.append("replacement_knowledge_constraint_no_longer_valid")

            current_difficulty = profiles.get(source.question_id)
            replacement_difficulty = profiles.get(replacement.id)
            if current_difficulty is None or replacement_difficulty is None:
                errors.append("replacement_difficulty_no_longer_valid")
                continue
            target = contract.get("target_difficulty")
            direction = contract.get("difficulty_direction") or "same"
            if target is not None:
                if replacement_difficulty != target:
                    errors.append("replacement_difficulty_no_longer_valid")
            elif direction == "easier" and not replacement_difficulty < current_difficulty:
                errors.append("replacement_difficulty_no_longer_valid")
            elif direction == "harder" and not replacement_difficulty > current_difficulty:
                errors.append("replacement_difficulty_no_longer_valid")
            elif direction == "same" and replacement_difficulty != current_difficulty:
                errors.append("replacement_difficulty_no_longer_valid")
        return list(dict.fromkeys(errors))

    def preview(self, request: PaperChangeRequest) -> PaperChangePreview:
        self._legacy_pending_guard()
        version_id = self._current_version_id()
        version = self.session.get(Paper, version_id)
        if version is None:
            raise PaperChangeServiceError("paper_not_found", "当前试卷版本不存在。")

        items = list(
            self.session.scalars(
                select(PaperItem)
                .where(PaperItem.paper_id == version.id)
                .order_by(PaperItem.position)
            ).all()
        )
        if not items:
            raise PaperChangeServiceError("paper_empty", "当前试卷没有题目。")

        analysis = analyze_paper(self.session, paper_id=version.id)
        if not analysis.ok:
            return PaperChangePreview(
                ok=False,
                paper_id=self._root_paper_id(version),
                blocking_errors=analysis.blocking_errors,
            )

        profiles = _profiles(self.session)
        knowledge, names = _knowledge(self.session)
        candidates = self._all_candidates(profiles)
        items_by_position = {item.position: item for item in items}
        question_types = dict(
            self.session.execute(select(Question.id, Question.question_type)).all()
        )
        occupied = {item.question_id for item in items}
        warnings: list[str] = []
        errors: list[str] = []
        questions: list[str] = []
        score_locks: set[int] = set()
        locked_add_ids: set[int] = set()

        operations, confirmation_contracts = self._load_pending_operations(version.id)
        # Recompute score balancing when a pending plan is patched with a new target total.
        if request.target_total_score is not None:
            operations = [
                operation
                for operation in operations
                if operation.type != "change_score"
            ]

        for change in request.operations:
            if isinstance(change, ReplaceQuestionChange):
                position = self._resolve_position(version.id, change.target)
                target = items_by_position[position]
                selected, error, replacement_warnings = self._select_replacement(
                    version=version,
                    items_by_position=items_by_position,
                    position=position,
                    change=change,
                    candidates=candidates,
                    profiles=profiles,
                    knowledge=knowledge,
                    occupied=occupied,
                )
                warnings.extend(replacement_warnings)
                if error or selected is None:
                    errors.append(error or "replacement_candidate_not_found")
                    continue
                operation = AdjustmentOperation(
                    type="replace_question",
                    position=position,
                    section=canonical_question_type(selected.question_type),
                    old_question_id=target.question_id,
                    new_question_id=selected.id,
                    score_before=float(target.score),
                    score_after=float(target.score),
                )
                self._merge_source_operation(
                    operations,
                    operation,
                    score_locks=score_locks,
                )
                confirmation_contracts[position] = {
                    "difficulty_direction": change.difficulty_direction or "same",
                    "target_difficulty": change.target_difficulty,
                    "preserve_knowledge_points": change.preserve_knowledge_points,
                    "required_knowledge_node_ids": (
                        sorted(knowledge[target.question_id])
                        if change.preserve_knowledge_points
                        else []
                    ),
                }
                occupied.add(selected.id)

            elif isinstance(change, RemoveQuestionChange):
                position = self._resolve_position(version.id, change.target)
                target = items_by_position[position]
                confirmation_contracts.pop(position, None)
                self._merge_source_operation(
                    operations,
                    AdjustmentOperation(
                        type="remove_question",
                        position=position,
                        old_question_id=target.question_id,
                        score_before=float(target.score),
                        score_after=0,
                    ),
                    score_locks=score_locks,
                )

            elif isinstance(change, ChangeQuestionScoreChange):
                position = self._resolve_position(version.id, change.target)
                target = items_by_position[position]
                self._merge_source_operation(
                    operations,
                    AdjustmentOperation(
                        type="change_score",
                        position=position,
                        old_question_id=target.question_id,
                        score_before=float(target.score),
                        score_after=float(change.score),
                    ),
                    score_locked=True,
                    score_locks=score_locks,
                )

            elif isinstance(change, AddQuestionsChange):
                canonical = canonical_question_type(change.question_type)
                resolved_score, error, clarification = self._infer_add_score(
                    items,
                    question_type=canonical,
                    explicit_score=change.score,
                )
                if error or resolved_score is None:
                    errors.append(error or "add_question_score_required")
                    questions.extend(clarification)
                    continue
                insert_position = self._section_insert_position(items, canonical)
                matches = [
                    question
                    for question in candidates
                    if question.id not in occupied
                    and canonical_question_type(question.question_type) == canonical
                    and _question_in_version_scope(
                        self.session,
                        version,
                        question,
                        knowledge[question.id],
                    )
                ]
                matches.sort(key=lambda question: question.id)
                if len(matches) < change.count:
                    errors.append("add_question_candidate_not_found")
                    continue
                for selected in matches[: change.count]:
                    operation = AdjustmentOperation(
                        type="add_question",
                        position=insert_position,
                        section=canonical,
                        old_question_id=None,
                        new_question_id=selected.id,
                        score_before=0,
                        score_after=float(resolved_score),
                    )
                    operations.append(operation)
                    if change.score is not None:
                        locked_add_ids.add(id(operation))
                    occupied.add(selected.id)

            elif isinstance(change, ChangeQuestionTypeDistributionChange):
                canonical_changes = {
                    canonical_question_type(name): value
                    for name, value in change.changes.items()
                    if value != 0
                }
                if sum(canonical_changes.values()) != 0:
                    errors.append("question_count_change_not_balanced")
                    continue
                reserved = {
                    operation.position
                    for operation in operations
                    if operation.type != "add_question"
                }
                sources: list[PaperItem] = []
                for source_type, delta in canonical_changes.items():
                    if delta >= 0:
                        continue
                    pool = [
                        item
                        for item in items
                        if canonical_question_type(item.section) == source_type
                        and item.position not in reserved
                    ]
                    if len(pool) < -delta:
                        errors.append("insufficient_source_question_type")
                        continue
                    chosen = pool[: -delta]
                    sources.extend(chosen)
                    reserved.update(item.position for item in chosen)
                source_index = 0
                for target_type, delta in canonical_changes.items():
                    if delta <= 0:
                        continue
                    for _ in range(delta):
                        if source_index >= len(sources):
                            errors.append("question_type_change_not_balanced")
                            break
                        source = sources[source_index]
                        source_index += 1
                        matches = [
                            question
                            for question in candidates
                            if question.id not in occupied
                            and canonical_question_type(question.question_type) == target_type
                            and _question_in_version_scope(
                                self.session,
                                version,
                                question,
                                knowledge[question.id],
                            )
                        ]
                        matches.sort(key=lambda question: question.id)
                        if not matches:
                            errors.append("replacement_candidate_not_found")
                            continue
                        selected = matches[0]
                        operation = AdjustmentOperation(
                            type="replace_question",
                            position=source.position,
                            section=target_type,
                            old_question_id=source.question_id,
                            new_question_id=selected.id,
                            score_before=float(source.score),
                            score_after=float(source.score),
                        )
                        self._merge_source_operation(
                            operations,
                            operation,
                            score_locks=score_locks,
                        )
                        occupied.add(selected.id)

        removed_positions = {
            operation.position
            for operation in operations
            if operation.type == "remove_question"
        }
        if len(removed_positions) >= len(items):
            errors.append("cannot_remove_all_questions")

        if request.target_total_score is not None and not errors:
            entries, balance_error = self._rebalance_to_total(
                items=items,
                operations=operations,
                question_types=question_types,
                target_total_score=float(request.target_total_score),
                score_locks=score_locks,
                locked_add_ids=locked_add_ids,
            )
            if balance_error:
                errors.append(balance_error)
                questions.append(
                    "当前修改后无法按0.5分粒度自动平衡到目标总分，请明确希望调整哪一类题目的分值。"
                )
        else:
            entries = self._planned_entries(
                items,
                operations,
                question_types=question_types,
                score_locks=score_locks,
                locked_add_ids=locked_add_ids,
            )

        if not operations and not errors:
            return PaperChangePreview(
                ok=True,
                paper_id=self._root_paper_id(version),
                warnings=["paper_change_noop"],
            )

        validation_errors = validate_adjustment_operations(
            self.session,
            version=version,
            items=items,
            operations=operations,
            require_source_match=False,
        )
        errors.extend(validation_errors)
        errors = list(dict.fromkeys(errors))
        warnings = list(dict.fromkeys(warnings))
        questions = list(dict.fromkeys(questions))

        after = self._summary_from_entries(
            entries,
            profiles=profiles,
            knowledge=knowledge,
            names=names,
            paper_id=version.id,
        )
        if (
            request.target_total_score is not None
            and abs(after.score_total - request.target_total_score) > 1e-9
        ):
            errors.append("target_total_score_not_satisfied")
            errors = list(dict.fromkeys(errors))

        satisfied: list[str] = []
        if not errors:
            satisfied = [
                "scope_preserved",
                "no_duplicate_question_ids",
                "approved_active_current_profile",
                "requested_paper_changes_resolved",
            ]
            if request.target_total_score is not None:
                satisfied.append("target_total_score_satisfied")

        record = AdjustmentPlanRecord(
            paper_id=self._root_paper_id(version),
            base_paper_version_id=version.id,
            operations_json=[
                {
                    **operation.model_dump(mode="json"),
                    **(
                        {"_confirmation_contract": confirmation_contracts[operation.position]}
                        if operation.type == "replace_question"
                        and operation.position in confirmation_contracts
                        else {}
                    ),
                }
                for operation in operations
            ],
            before_summary_json=analysis.model_dump(
                include={
                    "score_total",
                    "question_count",
                    "question_type_distribution",
                    "difficulty_distribution",
                    "knowledge_distribution",
                }
            ),
            after_summary_json=after.model_dump(mode="json"),
            satisfied_constraints_json=satisfied,
            warnings_json=["paper_change_preview_only", *warnings],
            blocking_errors_json=errors,
            status="blocked" if errors else "pending",
        )
        self.session.add(record)
        self.session.flush()

        plan = AdjustmentPlan(
            plan_id=record.id,
            paper_id=record.paper_id,
            base_paper_version_id=record.base_paper_version_id,
            operations=[
                AdjustmentOperation.model_validate(value)
                for value in record.operations_json
            ],
            before_summary=PaperSummary.model_validate(record.before_summary_json),
            after_summary=PaperSummary.model_validate(record.after_summary_json),
            satisfied_constraints=record.satisfied_constraints_json,
            warnings=record.warnings_json,
            blocking_errors=record.blocking_errors_json,
            status=record.status,
            created_at=record.created_at,
        )

        if not errors and self.store and self.conversation_id and hasattr(self.store, "set_adjustment"):
            self.store.set_adjustment(self.conversation_id, record.id)

        return PaperChangePreview(
            ok=not errors,
            paper_id=self._root_paper_id(version),
            plan=plan,
            warnings=plan.warnings,
            blocking_errors=errors,
            clarification_questions=questions,
        )


