"""Deterministic policy decisions used by the Teacher Agent runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRuntimePolicy:
    """Provide runtime limits and boundary decisions without executing work."""

    max_tool_rounds: int = 8

    clarification_blocking_errors: frozenset[str] = frozenset({
        "knowledge_scope_conflict", "knowledge_unknown", "knowledge_ambiguous",
        "knowledge_scope_uncertain", "missing_scope", "missing_exam_scope",
        "scope_not_found", "scope_ambiguous", "missing_total_score",
        "missing_difficulty_ratio", "question_count_mismatch", "score_total_mismatch",
        "question_type_invalid", "candidate_insufficient", "insufficient_candidates",
        "generation_partial_patch_required", "score_rebalance_ambiguous",
        "add_question_score_required", "add_question_score_ambiguous",
        "paper_observation_required", "no_current_paper", "no_pending_generation",
        "no_pending_action", "no_pending_adjustment", "curriculum_scope_unresolved",
        "question_bank_scope_unresolved",
    })
    pending_preservation_errors: frozenset[str] = frozenset({
        "pending_replacement_exists", "pending_generation_exists",
        "pending_adjustment_exists", "legacy_pending_replacement_exists",
    })

    def can_start_round(self, round_number: int) -> bool:
        return round_number <= self.max_tool_rounds

    def should_stop_after_tool(
        self,
        *,
        clarification_boundary: bool,
        terminal_boundary: bool,
        repeated_validation_boundary: bool,
    ) -> bool:
        return (
            clarification_boundary
            or terminal_boundary
            or repeated_validation_boundary
        )

    def is_clarification_error(self, code: str) -> bool:
        return code in self.clarification_blocking_errors

    def preserves_pending_state(self, code: str) -> bool:
        return code in self.pending_preservation_errors
