"""Teacher-provenance guards for model-supplied Tool arguments."""

import re
from typing import Any


_KNOWLEDGE_TARGET = r"(?:知识点|考点)"
_PRESERVE_ACTION = (
    r"(?:保持|保留|不变|别变|不要变|不动|别动|不要动|"
    r"不改|别改|不要改|不调整|别调整|不要调整)"
)
_GAP = r"[^。！？,，；;]{0,6}"
_PRESERVE_KNOWLEDGE_POINTS_PATTERN = re.compile(
    rf"{_KNOWLEDGE_TARGET}{_GAP}{_PRESERVE_ACTION}"
    rf"|{_PRESERVE_ACTION}{_GAP}(?:原)?{_KNOWLEDGE_TARGET}"
)
_TOTAL_SCORE_PATTERNS = (
    re.compile(r"(?:总分|满分|卷面(?:总)?分)[^。！？,，；;]{0,8}?\d{1,3}\s*分?"),
    re.compile(r"\d{1,3}\s*分(?:制|的)?\s*(?:试卷|卷子|测试|考试)"),
    re.compile(r"^\s*\d{1,3}\s*分?\s*(?:就可以|即可|可以|就行|行)?[。！？]?\s*$"),
)
_QUESTION_COUNT_PATTERNS = (
    re.compile(r"(?:共|总共|一共|合计|总计)\s*\d{1,3}\s*(?:道|题)"),
    re.compile(r"(?:题量|总题数|题目数量)\s*[:：]?\s*\d{1,3}"),
    re.compile(r"^\s*\d{1,3}\s*(?:道|题)\s*(?:就可以|即可|可以|就行|行)?[。！？]?\s*$"),
)
_QUESTION_TYPE_COUNT_PATTERNS = (
    re.compile(r"(?:选择题|填空题|计算题|证明题)[^。！？,，；;]{0,8}?\d{1,3}\s*(?:道|题)?"),
    re.compile(r"(?:选择题|填空题|计算题|证明题)[^。！？,，；;]{0,8}?[一二三四五六七八九十两]+\s*(?:道|题)"),
    re.compile(r"(?:\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)\s*(?:选择题|填空题|计算题|证明题)"),
)


def _explicit_preserve_knowledge_points_requested(message: str) -> bool:
    return _PRESERVE_KNOWLEDGE_POINTS_PATTERN.search(message) is not None


def _matches_any(message: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(message) is not None for pattern in patterns)


def _apply_explicit_opt_in_guards(
    *, tool_name: str, arguments: dict[str, Any], message: str,
) -> dict[str, Any]:
    """Remove executable constraints that have no explicit teacher provenance."""
    updated = dict(arguments)
    if tool_name == "prepare_generation_plan":
        if "total_score" in updated and not _matches_any(message, _TOTAL_SCORE_PATTERNS):
            updated.pop("total_score", None)
        if "question_count" in updated and not _matches_any(message, _QUESTION_COUNT_PATTERNS):
            updated.pop("question_count", None)
        if not _matches_any(message, _QUESTION_TYPE_COUNT_PATTERNS):
            updated.pop("question_type_requirements", None)
            updated.pop("question_type_patches", None)

    if tool_name != "preview_paper_changes" or _explicit_preserve_knowledge_points_requested(message):
        return updated
    raw_operations = updated.get("operations")
    if not isinstance(raw_operations, list):
        return updated
    operations: list[Any] = []
    changed = False
    for raw in raw_operations:
        operation = dict(raw) if isinstance(raw, dict) else raw
        if (
            isinstance(operation, dict)
            and operation.get("type") == "replace_question"
            and operation.get("preserve_knowledge_points") is True
        ):
            operation["preserve_knowledge_points"] = False
            changed = True
        operations.append(operation)
    if changed:
        updated["operations"] = operations
    return updated
