"""Deterministic current-paper grounding rules.

This module deliberately decides evidence requirements, not semantic intent or
question-address resolution.  Address extraction remains a positive hint in
the runtime; it is never a prerequisite for grounding.
"""

from dataclasses import dataclass

from calculus_agent.papers.addressing import QuestionAddress


_PAPER_EVIDENCE_TERMS = (
    "试卷", "本卷", "这套卷", "这张卷", "当前卷", "当前题", "这道题", "该题",
    "第", "题目", "题干", "题型", "总分", "满分", "分值", "分数",
    "难度", "知识点", "考点", "总题数", "题数", "多少题", "多少道题",
)
_PAPER_MUTATION_TERMS = ("换", "替换", "删除", "删掉", "移除", "修改", "调整")


@dataclass(frozen=True)
class GroundingDecision:
    requires_current_paper_evidence: bool
    required_evidence: frozenset[str]
    available_evidence: frozenset[str]

    @property
    def has_fresh_observation(self) -> bool:
        return (
            bool(self.required_evidence)
            and self.required_evidence.issubset(
                self.available_evidence
            )
        )

    @property
    def read_required(self) -> bool:
        return (
            self.requires_current_paper_evidence
            and not self.has_fresh_observation
        )


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
        observed_paper_evidence: dict[str, set[str]],
    ) -> GroundingDecision:

        requires = cls.requires_current_paper_evidence(
            message=message,
            addresses=addresses,
            positions=positions,
        )

        required_evidence = (
            cls.required_evidence_for_request(
                message=message,
                addresses=addresses,
                positions=positions,
            )
            if requires
            else frozenset()
        )

        available_evidence = (
            frozenset(
                observed_paper_evidence.get(
                    current_version_id,
                    set(),
                )
            )
            if current_version_id
            else frozenset()
        )

        return GroundingDecision(
            requires_current_paper_evidence=requires,
            required_evidence=required_evidence,
            available_evidence=available_evidence,
        )

    @classmethod
    def required_evidence_for_request(
        cls,
        *,
        message: str,
        addresses: list[QuestionAddress],
        positions: list[int],
    ) -> frozenset[str]:
        if addresses:
            return frozenset(
                f"question_detail:address:{address.section_type}:{address.section_order}"
                for address in addresses
            )

        if positions:
            return frozenset(
                f"question_detail:position:{position}"
                for position in positions
            )

        single_question_reference = (
            ("第" in message and "题" in message)
            or any(term in message for term in ("这道题", "该题", "当前题"))
        )
        if single_question_reference:
            # This sentinel is never registered as evidence. The Coordinator
            # replaces the unresolved reference with exact Tool-returned
            # addresses/positions after a targeted read.
            return frozenset({"question_detail:unresolved_reference"})

        required: set[str] = set()
        if "总分" in message or "满分" in message:
            required.add("score_total")
        if "题数" in message or "多少题" in message or "多少道题" in message:
            required.add("question_count")
        if "题型" in message:
            required.add("question_type_distribution")
        if "难度" in message:
            required.add("difficulty_distribution")
        if "知识点" in message or "考点" in message:
            required.add("knowledge_distribution")
        if "分析" in message or "统计" in message:
            required.update({
                "score_total",
                "question_count",
                "question_type_distribution",
                "difficulty_distribution",
                "knowledge_distribution",
            })

        return frozenset(required or {"question_overview"})
