"""Shared question-type contract for blueprints, parsers, and bank queries."""

from __future__ import annotations


QUESTION_TYPE_ALIASES = {
    "selection": "选择题",
    "single_choice": "选择题",
    "multiple_choice": "多选题",
    "fill_blank": "填空题",
    "calculation": "计算题",
    "proof": "证明题",
    "qa": "解答题",
    "short_answer": "解答题",
    "subjective": "解答题",
    "other": "解答题",
    "unknown": "解答题",
    "选择": "选择题",
    "单选题": "选择题",
    "选择题": "选择题",
    "多选题": "多选题",
    "填空": "填空题",
    "填空题": "填空题",
    "计算": "计算题",
    "计算题": "计算题",
    "证明": "证明题",
    "证明题": "证明题",
    "问答": "解答题",
    "问答题": "解答题",
    "解答": "解答题",
    "解答题": "解答题",
}

PAPER_QUESTION_TYPES = (
    "选择题", "多选题", "填空题", "计算题", "证明题", "解答题",
)
ALLOWED_QUESTION_TYPES = set(PAPER_QUESTION_TYPES)


def canonical_question_type(value: str) -> str:
    return QUESTION_TYPE_ALIASES.get(value.strip(), value.strip())
