"""Deterministic current-paper grounding rules.

This module deliberately decides evidence requirements, not semantic intent or
question-address resolution.  Address extraction remains a positive hint in
the runtime; it is never a prerequisite for grounding.
"""

from dataclasses import dataclass

from calculus_agent.papers.addressing import QuestionAddress


_PAPER_EVIDENCE_TERMS = (
    "试卷", "本卷", "这张卷", "当前卷", "当前题", "这道题", "该题",
    "第", "题目", "题干", "分值", "分数", "难度", "知识点", "考点",
)
_PAPER_MUTATION_TERMS = ("换", "替换", "删除", "删掉", "移除", "修改", "调整")


@dataclass(frozen=True)
class GroundingDecision:
    requires_current_paper_evidence: bool
    has_fresh_observation: bool

    @property
    def read_required(self) -> bool:
        return self.requires_current_paper_evidence and not self.has_fresh_observation


class GroundingPolicy:
    """Enforce the current-paper evidence invariant without an LLM gate."""

    @staticmethod
    def requires_current_paper_evidence(
        *,
        message: str,
        addresses: list[QuestionAddress],
        positions: list[int],
    ) -> bool:
        if addresses or positions:
            return True
        # A bare "第 N 题" still denotes the active paper when one exists.
        if "第" in message and "题" in message:
            return True
        if any(term in message for term in _PAPER_EVIDENCE_TERMS):
            return True
        return (
            any(term in message for term in _PAPER_MUTATION_TERMS)
            and any(term in message for term in ("题", "分", "卷"))
        )

    @classmethod
    def evaluate(
        cls,
        *,
        message: str,
        addresses: list[QuestionAddress],
        positions: list[int],
        current_version_id: str | None,
        observed_read_versions: set[str],
    ) -> GroundingDecision:
        requires = cls.requires_current_paper_evidence(
            message=message, addresses=addresses, positions=positions
        )
        return GroundingDecision(
            requires_current_paper_evidence=requires,
            has_fresh_observation=bool(
                current_version_id and current_version_id in observed_read_versions
            ),
        )
