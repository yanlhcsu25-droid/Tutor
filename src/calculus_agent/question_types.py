"""Shared question-type contract for blueprints, parsers, and bank queries."""

from __future__ import annotations


# 正式 canonical 题型（系统支持、可入组卷候选池）。
# 「解答题」已废弃：自由作答类在微积分中统一归为「计算题」；
# 不再存在「解答题」这一 canonical 目标，unknown 也保持 unknown（不映射为任何正式题型）。
QUESTION_TYPE_ALIASES = {
    "selection": "选择题",
    "single_choice": "选择题",
    "multiple_choice": "多选题",
    "fill_blank": "填空题",
    "calculation": "计算题",
    "proof": "证明题",
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
}

PAPER_QUESTION_TYPES = (
    "选择题", "多选题", "填空题", "计算题", "证明题",
)
ALLOWED_QUESTION_TYPES = set(PAPER_QUESTION_TYPES)


def canonical_question_type(value: str) -> str:
    return QUESTION_TYPE_ALIASES.get(value.strip(), value.strip())
