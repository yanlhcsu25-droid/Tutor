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
_EXPLICIT_GENERATION_COUNT = re.compile(
    r"(?:生成|出|来|一套|共|总共|一共)[^。！？,，；;0-9]{0,10}"
    r"(?P<count>\d{1,3})\s*(?:道|题)"
)
_EXPLICIT_GENERATION_SCORE = re.compile(
    r"(?P<score>\d{1,3})\s*分(?:制|的)?\s*"
    r"(?:期中|期末|试卷|卷子|测试|考试|练习卷|测试卷)"
)
_ALL_QUESTION_TYPE_PATTERN = re.compile(
    r"(?:全部|均|全都|都是|全是)\s*(?:为|是)?\s*"
    r"(?P<question_type>选择题|填空题|计算题|证明题)"
)
_EXPLICIT_TYPE_COUNT_PATTERNS = (
    re.compile(
        r"(?P<question_type>选择题|填空题|计算题|证明题)"
        r"[^。！？,，；;0-9一二三四五六七八九十两]{0,8}"
        r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)"
    ),
    re.compile(
        r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)\s*"
        r"(?P<question_type>选择题|填空题|计算题|证明题)"
    ),
)
_QUESTION_TYPE_COUNT_PATTERNS = (
    re.compile(r"(?:选择题|填空题|计算题|证明题)[^。！？,，；;]{0,8}?\d{1,3}\s*(?:道|题)?"),
    re.compile(r"(?:选择题|填空题|计算题|证明题)[^。！？,，；;]{0,8}?[一二三四五六七八九十两]+\s*(?:道|题)"),
    re.compile(r"(?:\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)\s*(?:选择题|填空题|计算题|证明题)"),
)


def _explicit_generation_count(message: str) -> int | None:
    matches = list(_EXPLICIT_GENERATION_COUNT.finditer(message))
    return int(matches[-1].group("count")) if matches else None


def _count_value(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if raw == "十":
        return 10
    if "十" in raw:
        left, right = raw.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits[raw]


def _explicit_question_type_counts(message: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pattern in _EXPLICIT_TYPE_COUNT_PATTERNS:
        for match in pattern.finditer(message):
            counts[match.group("question_type")] = _count_value(match.group("count"))

    all_match = _ALL_QUESTION_TYPE_PATTERN.search(message)
    total_count = _explicit_generation_count(message)
    if all_match is not None and total_count is not None:
        counts = {all_match.group("question_type"): total_count}
    return counts


def explicit_generation_constraint_mismatches(
    arguments: dict[str, Any],
    message: str,
) -> list[dict[str, Any]]:
    """Report any explicit type distribution omitted or changed by the model."""
    expected_counts = _explicit_question_type_counts(message)
    if not expected_counts:
        return []

    requirements = arguments.get("question_type_requirements")
    actual_counts = {
        item.get("question_type"): item.get("count")
        for item in requirements or []
        if isinstance(item, dict)
    }
    total_count = _explicit_generation_count(message)
    complete_distribution = (
        total_count is not None
        and sum(expected_counts.values()) == total_count
    )
    if all(
        actual_counts.get(question_type) == count
        for question_type, count in expected_counts.items()
    ) and (not complete_distribution or actual_counts == expected_counts):
        return []

    expected = [
        {"question_type": question_type, "count": count}
        for question_type, count in expected_counts.items()
    ]
    return [{"field": "question_type_requirements", "expected": expected}]


# Backward-compatible name for existing callers/tests.
explicit_generation_constraint_omissions = explicit_generation_constraint_mismatches


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
        explicit_count = _explicit_generation_count(message)
        score_match = _EXPLICIT_GENERATION_SCORE.search(message)
        if explicit_count is not None:
            updated["question_count"] = explicit_count
        if score_match:
            updated["total_score"] = int(score_match.group("score"))
        if "total_score" in updated and not (
            score_match or _matches_any(message, _TOTAL_SCORE_PATTERNS)
        ):
            updated.pop("total_score", None)
        if "question_count" in updated and not (
            explicit_count is not None or _matches_any(message, _QUESTION_COUNT_PATTERNS)
        ):
            updated.pop("question_count", None)
        if not (
            _matches_any(message, _QUESTION_TYPE_COUNT_PATTERNS)
            or _ALL_QUESTION_TYPE_PATTERN.search(message)
        ):
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
