"""Shared question-type contract for blueprints, parsers, and bank queries.

This module is the SINGLE SOURCE OF TRUTH for the question-type vocabulary.

Canonical question types (the only values allowed in the database, API
responses, and the frontend) are exactly:

    VALID_QUESTION_TYPES = {"选择题", "填空题", "计算题", "证明题", "unknown"}

Everything else — historical English aliases (``selection``, ``calculation``,
``proof``, ``fill_blank`` …), deprecated Chinese types (``多选题``,
``解答题``, ``简答题``, ``判断题``, ``其他`` …) — is mapped through
``QUESTION_TYPE_ALIASES`` to one of the five canonical values. Any value that
cannot be resolved falls back to ``"unknown"``; ``canonical_question_type``
never returns the raw input.

Rationale for deprecated types:
  * 解答题 / 简答题 / 问答题 / 问答 / 其他 / subjective … — free-form answer
    types are unified to ``unknown`` (they are no longer a supported canonical
    type; a human must re-classify, or the bank treats them as untyped).
  * 多选题 — semantically still a choice-type question, so it folds into
    ``选择题``.
"""

from __future__ import annotations

# 唯一合法的五个 canonical 题型。
VALID_QUESTION_TYPES: set[str] = {
    "选择题",
    "填空题",
    "计算题",
    "证明题",
    "unknown",
}

# 历史别名 → canonical。禁止在这里把 deprecated 类型列为 canonical 目标
# （例如不得出现 ``"多选题": "多选题"``）。无法识别的值不在此表内，
# 由 canonical_question_type 兜底为 "unknown"。
QUESTION_TYPE_ALIASES: dict[str, str] = {
    # ── 选择题 ──
    "selection": "选择题",
    "single_choice": "选择题",
    "multiple_choice": "选择题",
    "choice": "选择题",
    "choose": "选择题",
    "select": "选择题",
    "选择": "选择题",
    "单选题": "选择题",
    "多项选择题": "选择题",
    "多选题": "选择题",
    "选择题": "选择题",
    # ── 填空题 ──
    "fill_blank": "填空题",
    "fillblank": "填空题",
    "blank": "填空题",
    "填空": "填空题",
    "填空题": "填空题",
    # ── 计算题 ──
    "calculation": "计算题",
    "calculate": "计算题",
    "calculation_question": "计算题",
    "计算": "计算题",
    "计算题": "计算题",
    # ── 证明题 ──
    "proof": "证明题",
    "proof_question": "证明题",
    "prove": "证明题",
    "证明": "证明题",
    "证明题": "证明题",
    # ── unknown（废弃类型统一归 unknown，不再作为正式题型）──
    "解答题": "unknown",
    "简答题": "unknown",
    "问答题": "unknown",
    "问答": "unknown",
    "解答": "unknown",
    "判断题": "unknown",
    "其他": "unknown",
    "other": "unknown",
    "subjective": "unknown",
    "short_answer": "unknown",
    "composite": "unknown",
    "未知": "unknown",
    "unknown": "unknown",
    "UNKNOWN": "unknown",
    "Unknown": "unknown",
}

# 大小写无关的别名查找表（处理 Proof / PROOF / proof 等）。
_QUESTION_TYPE_ALIASES_LOWER: dict[str, str] = {
    k.lower(): v for k, v in QUESTION_TYPE_ALIASES.items()
}

# 组卷可用的题型（unknown 不是一种可配置的试卷题型）。
PAPER_QUESTION_TYPES = ("选择题", "填空题", "计算题", "证明题")

# 对外允许集合（含 unknown，与 VALID_QUESTION_TYPES 一致）。
ALLOWED_QUESTION_TYPES = set(VALID_QUESTION_TYPES)


def canonical_question_type(value: str | None) -> str:
    """Return the canonical question type for ``value``.

    The result is ALWAYS one of :data:`VALID_QUESTION_TYPES`. ``None``, empty
    strings, whitespace, unknown aliases, and any otherwise-unrecognized value
    all resolve to ``"unknown"``. The raw input is never returned.
    """
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s:
        return "unknown"
    if s in VALID_QUESTION_TYPES:
        return s
    if s in QUESTION_TYPE_ALIASES:
        return QUESTION_TYPE_ALIASES[s]
    low = s.lower()
    if low in _QUESTION_TYPE_ALIASES_LOWER:
        return _QUESTION_TYPE_ALIASES_LOWER[low]
    return "unknown"
