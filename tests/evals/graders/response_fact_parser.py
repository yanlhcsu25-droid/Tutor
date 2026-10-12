"""Scope-aware deterministic facts from Chinese paper summaries.

Parser deliberately avoids treating per-type counts or ordinal numbers as the
whole-paper question count. No LLM calls and no case-id specific rules.
"""

from __future__ import annotations

import re
from collections import Counter
from math import isclose
from typing import Any

_QTYPE = r"(?:选择题|填空题|计算题|证明题|解答题|判断题)"
_INT = r"\d+"
_DECIMAL = r"\d+(?:\.\d+)?"
_QNOUN = r"(?:道\s*)?题(?:目)?"

_TOTAL_COUNT_EXPLICIT = re.compile(
    rf"(?:总题数|总题量|题目总数|试题总数)\s*[:：=为是]?\s*({_INT})\s*{_QNOUN}"
    rf"|(?:整卷|全卷|本卷|整套(?:试卷)?|这套(?:试卷|卷)|当前(?:试卷|卷)|试卷)"
    rf"\s*(?:共包含|一共包含|共有|一共有|总共|一共|包含|共|有|为|是)\s*({_INT})\s*{_QNOUN}"
)
_TOTAL_COUNT_GENERIC = re.compile(rf"(?:共|总共|一共|包含|有)\s*({_INT})\s*{_QNOUN}")
_TOTAL_SCORE = re.compile(rf"(?:总分|满分|总成绩)\s*[:：=]?\s*(?:为|是|共)?\s*({_DECIMAL})\s*分")
_TYPE_FORWARD = re.compile(rf"(?<!\d)({_INT})\s*道?\s*({_QTYPE})")
_TYPE_BACKWARD = re.compile(
    rf"({_QTYPE})\s*[：:]?\s*[（(]?\s*(?:共|一共|还有|有|为|是)?\s*({_INT})\s*(?:道\s*题?|题)"
)
# Difficulty is parsed only with an explicit all-paper/universal qualifier.
_ALL_DIFFICULTY = re.compile(
    r"(?:所有|全部|全卷|整卷)\s*(?:的)?\s*(?:题目|试题|题)?\s*(?:的)?\s*"
    r"难度(?:系数|等级)?\s*(?:均为|均是|都是|都为|为|是|[:：])\s*([1-5])"
)
_DIFFICULTY_COUNT = re.compile(
    rf"难度(?:系数|等级)?\s*([1-5])\s*[:：]?\s*({_INT})\s*{_QNOUN}"
)


def normalize_reply(message: str) -> str:
    """Discard Markdown emphasis but keep punctuation and line boundaries."""
    return str(message).replace("**", "").replace("`", "").replace("__", "")


def whole_paper_counts(message: str) -> list[int]:
    normalized = normalize_reply(message)
    explicit_matches = list(_TOTAL_COUNT_EXPLICIT.finditer(normalized))
    explicit = [int(match.group(1) or match.group(2)) for match in explicit_matches]
    generic: list[int] = []
    for match in _TOTAL_COUNT_GENERIC.finditer(normalized):
        if any(a.start() <= match.start() and match.end() <= a.end() for a in explicit_matches):
            continue
        # '选择题（共4题）' is a type count, not the entire paper.
        prefix = normalized[max(0, match.start() - 35):match.start()].split("\n")[-1]
        if re.search(rf"{_QTYPE}\s*[：:（(]?\s*$", prefix):
            continue
        generic.append(int(match.group(1)))
    # Prioritize explicit total count when present, while still checking any
    # independently stated generic count for contradictions.
    return explicit + generic


def paper_scores(message: str) -> list[float]:
    return [float(m.group(1)) for m in _TOTAL_SCORE.finditer(normalize_reply(message))]


def type_counts(message: str) -> list[tuple[str, int]]:
    normalized = normalize_reply(message)
    claims: list[tuple[str, int]] = []
    spans: set[tuple[int, int]] = set()
    for m in _TYPE_FORWARD.finditer(normalized):
        claims.append((m.group(2), int(m.group(1))))
        spans.add(m.span())
    for m in _TYPE_BACKWARD.finditer(normalized):
        # do not double-count an overlap covered by an earlier claim
        if any(m.start() < end and start < m.end() for start, end in spans):
            continue
        claims.append((m.group(1), int(m.group(2))))
    return claims


def extract_response_facts(message: str) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    counts = whole_paper_counts(message)
    if counts:
        facts["question_count"] = counts[0]
    scores = paper_scores(message)
    if scores:
        value = scores[0]
        facts["total_score"] = int(value) if value.is_integer() else value
    types = type_counts(message)
    if types:
        # First claim, not last-write-wins; consistency check handles conflicts.
        distribution: dict[str, int] = {}
        for kind, number in types:
            distribution.setdefault(kind, number)
        facts["question_type_counts"] = distribution
    normalized = normalize_reply(message)
    difficulties = {
        match.group(1): int(match.group(2))
        for match in _DIFFICULTY_COUNT.finditer(normalized)
    }
    if difficulties:
        facts["difficulty_counts"] = difficulties
    else:
        m = _ALL_DIFFICULTY.search(normalized)
        if m and "question_count" in facts:
            facts["difficulty_counts"] = {str(m.group(1)): facts["question_count"]}
    return facts


def read_only_claim_errors(
    message: str, *, expected_count: int, expected_score: float | None,
    persisted_type_counts: Counter[str] | dict[str, int],
) -> list[str]:
    """Validate every explicit claim; do not let one correct sentence mask errors."""
    errors: list[str] = []
    for count in whole_paper_counts(message):
        if count != expected_count:
            errors.append(f"reply question count={count}, persisted={expected_count}")
    if expected_score is not None:
        for score in paper_scores(message):
            if not isclose(score, float(expected_score), abs_tol=0.01):
                errors.append(f"reply total score={score}, persisted={expected_score}")
    for kind, count in type_counts(message):
        # Only check types known to the fixture; an unexpected named type is
        # also a factual claim and must not silently pass.
        expected = int(persisted_type_counts.get(kind, 0))
        if count != expected:
            errors.append(f"reply {kind} count={count}, persisted={expected}")
    return errors
