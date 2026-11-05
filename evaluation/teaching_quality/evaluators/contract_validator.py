"""Deterministic audit of Judge output against supplied scoring contracts."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JudgeContractAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["VALID", "JUDGE_CONTRACT_VIOLATION", "INCONCLUSIVE"]
    rule_id: str | None = None
    evidence: list[str] = Field(default_factory=list)
    canonical_counts_match: bool | None = None


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？;；])\s*")
_CANONICAL_ACK = re.compile(
    r"(?:canonical|规范化|归一化).{0,100}(?:match|satisf|一致|满足|符合)",
    re.IGNORECASE,
)
_FORBIDDEN_SEPARATION_CLAIMS = (
    re.compile(
        r"(?:explicit\s+)?separation.{0,100}(?:not\s+preserved|missing|absent)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:no|without|missing).{0,50}(?:separate|separately).{0,50}(?:label|category|section)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:deviation|imbalance).{0,100}(?:distinct|separate).{0,50}(?:category|label|section)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:未|没有|缺少).{0,30}(?:独立|单独).{0,20}(?:题型|类别|标签|分区)"),
    re.compile(r"(?:独立|单独).{0,20}(?:题型|类别|标签|分区).{0,30}(?:未保留|缺失|没有)"),
)
_PENALTY_CLAIMS = re.compile(
    r"(?:result(?:ing)?\s+in|therefore|thus|deviation|lower|penal|扣分|因此|导致|偏差|不平衡)",
    re.IGNORECASE,
)
_TARGET_COUNT_CLAIM = re.compile(
    r"(?:target|requested(?:\s+count)?|目标|要求).{0,20}?(?P<count>\d+)\s*"
    r"(?P<type>calculation(?:-type)?|proof|计算题?|证明题?)",
    re.IGNORECASE,
)
_TYPE_WORDS = {
    "calculation": "计算题", "calculation-type": "计算题", "proof": "证明题",
    "计算": "计算题", "计算题": "计算题", "证明": "证明题", "证明题": "证明题",
}


def canonical_actual_counts(actual_output: dict[str, Any]) -> dict[str, int]:
    from calculus_agent.question_types import canonical_question_type

    paper = actual_output.get("paper") or {}
    raw = paper.get("question_type_counts") or {}
    counts: dict[str, int] = {}
    for question_type, count in raw.items():
        canonical = canonical_question_type(str(question_type))
        counts[canonical] = counts.get(canonical, 0) + int(count)
    return counts


def _alias_terms(aliases: dict[str, str]) -> list[str]:
    canonical_frequency: dict[str, int] = {}
    for canonical in aliases.values():
        canonical_frequency[canonical] = canonical_frequency.get(canonical, 0) + 1
    return [
        alias.removesuffix("题")
        for alias, canonical in aliases.items()
        if alias != canonical and canonical_frequency[canonical] > 1
    ]


def audit_judge_contract(
    judge_payload: dict[str, Any],
    *,
    judge_version: str,
    suite: str,
    canonical_aliases: dict[str, str],
    canonical_requested_counts: dict[str, int],
    actual_canonical_counts: dict[str, int],
) -> JudgeContractAudit:
    """Flag only an explicit alias-label penalty supported by multiple facts.

    Count equality or a low score alone is never a violation. A violation needs:
    the v2 paper contract, exact canonical counts, an applicable non-identity
    alias, and a rationale clause that both demands separate alias labeling and
    presents that demand as a scoring defect. Ambiguous alias discussion is
    retained as INCONCLUSIVE and does not trigger retry.
    """
    if judge_version != "v2" or suite != "paper_quality":
        return JudgeContractAudit(status="VALID")

    counts_match = bool(canonical_requested_counts) and (
        canonical_requested_counts == actual_canonical_counts
    )
    aliases = _alias_terms(canonical_aliases)
    rationale = str((judge_payload.get("rationale") or {}).get("question_type_balance") or "")
    overall = str(judge_payload.get("overall_comment") or "")
    text = " ".join((rationale, overall)).strip()
    applicable_aliases = [alias for alias in aliases if alias and alias in text]
    if not counts_match or not applicable_aliases:
        return JudgeContractAudit(status="VALID", canonical_counts_match=counts_match)

    sentences = [sentence for sentence in _SENTENCE_SPLIT.split(text) if sentence]
    contradictory_count_claims = []
    for match in _TARGET_COUNT_CLAIM.finditer(text):
        claimed_type = _TYPE_WORDS.get(match.group("type").lower())
        claimed_count = int(match.group("count"))
        if (
            claimed_type in canonical_requested_counts
            and claimed_count != canonical_requested_counts[claimed_type]
        ):
            contradictory_count_claims.append(match.group(0))
    if contradictory_count_claims:
        return JudgeContractAudit(
            status="JUDGE_CONTRACT_VIOLATION",
            rule_id="canonical_alias_count_double_counted",
            evidence=contradictory_count_claims,
            canonical_counts_match=True,
        )

    violating_sentences = [
        sentence for sentence in sentences
        if any(alias in sentence for alias in applicable_aliases)
        and any(pattern.search(sentence) for pattern in _FORBIDDEN_SEPARATION_CLAIMS)
        and _PENALTY_CLAIMS.search(sentence)
    ]
    # Some responses acknowledge the canonical match in one sentence and state
    # the forbidden label penalty in the next. Preserve both as traceable proof.
    canonical_evidence = [sentence for sentence in sentences if _CANONICAL_ACK.search(sentence)]
    if violating_sentences and (
        canonical_evidence
        or "question_type_imbalance" in (judge_payload.get("failure_tags") or [])
    ):
        return JudgeContractAudit(
            status="JUDGE_CONTRACT_VIOLATION",
            rule_id="canonical_alias_has_no_separate_label_requirement",
            evidence=[*canonical_evidence, *violating_sentences],
            canonical_counts_match=True,
        )

    if "question_type_imbalance" in (judge_payload.get("failure_tags") or []):
        return JudgeContractAudit(
            status="INCONCLUSIVE",
            rule_id="canonical_alias_rationale_requires_review",
            evidence=[rationale] if rationale else [],
            canonical_counts_match=True,
        )
    return JudgeContractAudit(status="VALID", canonical_counts_match=True)
