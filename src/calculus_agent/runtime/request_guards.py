"""Teacher-provenance guards for model-supplied Tool arguments."""

import re
from typing import Any

from calculus_agent.question_types import canonical_question_type
from calculus_agent.runtime.response_policy import ResponsePolicy


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
    re.compile(r"(?:题量|题数|总题数|题目数量)[^。！？,，；;]{0,8}?\d{1,3}\s*(?:道|题)?"),
    re.compile(r"^\s*\d{1,3}\s*(?:道|题)\s*(?:就可以|即可|可以|就行|行)?[。！？]?\s*$"),
)
_EXPLICIT_GENERATION_COUNT = re.compile(
    r"(?:生成|出|来|安排|准备|给我|只要|需要|要|做|一套|共|总共|一共)"
    r"[^。！？,，；;0-9一二三四五六七八九十两]{0,10}"
    r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)"
)
_EXPLICIT_GENERATION_SCORE = re.compile(
    r"(?P<score>\d{1,3})\s*分(?:制|的)?\s*"
    r"(?:期中|期末|试卷|卷子|测试|考试|练习卷|测试卷)"
)
_EXPLICIT_QUESTION_COUNT_PATCH = re.compile(
    r"(?:题量|题数|总题数|题目数量)[^。！？,，；;]{0,8}?"
    r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)?"
)
_EXPLICIT_QUESTION_COUNT_ACTION_PATCH = re.compile(
    r"(?:改成|改为|调整到)\s*"
    r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道\s*)?题"
)
_EXPLICIT_SCORE_PATCH = re.compile(
    r"(?:总分[^。！？,，；;]{0,8}?|改成|改为|降到|提高到|调整到)"
    r"(?P<score>\d{1,3})\s*分"
)
_COMPACT_GENERATION_COUNT_SCORE = re.compile(
    r"(?:生成|出|来|组卷|给我|只要|需要|要|做|共|总共|一共|改成|改为|调整到)"
    r"[^。！？,，；;]{0,20}?"
    r"(?:\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)"
    r"[^。！？]{0,10}?(?P<score>\d{1,3})\s*分"
)
_QUESTION_TYPE_VALUE = r"选择题?|填空题?|计算题?|证明题?|解答题?"
_ALL_QUESTION_TYPE_PATTERN = re.compile(
    r"(?:全部|均|全都|都是|全是|只要)\s*(?:为|是)?\s*"
    rf"(?P<question_type>{_QUESTION_TYPE_VALUE})"
)
_EXPLICIT_TYPE_COUNT_PATTERNS = (
    re.compile(
        rf"(?P<question_type>{_QUESTION_TYPE_VALUE})"
        r"[^。！？、,，；;0-9一二三四五六七八九十两]{0,8}"
        r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)?"
    ),
    re.compile(
        r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)?\s*"
        rf"(?P<question_type>{_QUESTION_TYPE_VALUE})"
    ),
)
_EXPLICIT_LESSON_COUNT_PATTERN = re.compile(
    r"(?P<count>\d{1,3}|[一二三四五六七八九十两]+)\s*课时"
)
_EXPLICIT_ASSESSMENT_SCORE_PATTERN = re.compile(
    r"(?:总分|满分)(?!钟)|\d{1,3}(?:\.\d+)?\s*分(?!钟)"
)
_QUESTION_TYPE_COUNT_PATTERNS = (
    re.compile(r"(?:选择题|填空题|计算题|证明题|解答题)[^。！？,，；;]{0,8}?\d{1,3}\s*(?:道|题)?"),
    re.compile(r"(?:选择题|填空题|计算题|证明题|解答题)[^。！？,，；;]{0,8}?[一二三四五六七八九十两]+\s*(?:道|题)"),
    re.compile(r"(?:\d{1,3}|[一二三四五六七八九十两]+)\s*(?:道|题)\s*(?:选择题|填空题|计算题|证明题|解答题)"),
)


def _explicit_generation_count(message: str) -> int | None:
    for pattern in (
        _EXPLICIT_GENERATION_COUNT,
        _EXPLICIT_QUESTION_COUNT_PATCH,
        _EXPLICIT_QUESTION_COUNT_ACTION_PATCH,
    ):
        matches = list(pattern.finditer(message))
        if matches:
            return _count_value(matches[-1].group("count"))
    return None


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


def _explicit_generation_score(message: str) -> int | None:
    patterns = [_EXPLICIT_GENERATION_SCORE, _EXPLICIT_SCORE_PATCH]
    if "每题" not in message:
        patterns.append(_COMPACT_GENERATION_COUNT_SCORE)
    for pattern in patterns:
        match = pattern.search(message)
        if match is not None:
            return int(match.group("score"))
    return None


def _explicit_question_type_items(message: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for pattern_index, pattern in enumerate(_EXPLICIT_TYPE_COUNT_PATTERNS):
        for match in pattern.finditer(message):
            if (
                pattern_index == 0
                and match.end("count") == match.end()
                and message[match.end("question_type"):match.start("count")]
            ):
                # Unit-less compact notation is accepted only when adjacent
                # (选择4); do not reinterpret “计算题多一点” as count=1.
                continue
            if match.span() in seen:
                continue
            seen.add(match.span())
            raw_type = match.group("question_type")
            normalized_type = raw_type if raw_type.endswith("题") else raw_type + "题"
            items.append({
                "question_type": raw_type,
                "count": _count_value(match.group("count")),
                "canonical_question_type": canonical_question_type(normalized_type),
            })
    return items


def _explicit_question_type_counts(message: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in _explicit_question_type_items(message):
        canonical = item["canonical_question_type"]
        counts[canonical] = counts.get(canonical, 0) + item["count"]

    all_match = _ALL_QUESTION_TYPE_PATTERN.search(message)
    total_count = _explicit_generation_count(message)
    if all_match is not None and total_count is not None:
        question_type = all_match.group("question_type")
        if not question_type.endswith("题"):
            question_type += "题"
        counts = {canonical_question_type(question_type): total_count}
    return counts


def extract_generation_constraint_contract(message: str) -> dict[str, Any] | None:
    """Keep teacher wording and canonical executable constraints side by side."""
    raw_items = _explicit_question_type_items(message)
    canonical_counts = _explicit_question_type_counts(message)
    explicit_count = _explicit_generation_count(message)
    explicit_score = _explicit_generation_score(message)
    if not any((raw_items, explicit_count, explicit_score)):
        return None
    canonical_requirements = [
        {"question_type": question_type, "count": count}
        for question_type, count in canonical_counts.items()
    ]
    derived_count = sum(canonical_counts.values()) if canonical_counts else None
    return {
        "original_request": message,
        "raw": {
            "question_count": explicit_count,
            "total_score": explicit_score,
            "question_type_requirements": [
                {"question_type": item["question_type"], "count": item["count"]}
                for item in raw_items
            ],
        },
        "canonical": {
            "question_count": explicit_count or derived_count,
            "total_score": explicit_score,
            "question_type_requirements": canonical_requirements,
        },
        "provenance": {
            "source": "teacher_message",
            "strength": "hard",
            "question_count": "explicit" if explicit_count is not None else "derived_from_type_counts",
        },
    }


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


def explicit_generation_missing_count(message: str) -> bool:
    return bool(
        _ALL_QUESTION_TYPE_PATTERN.search(message)
        and _explicit_generation_count(message) is None
    )


def observed_generation_supply_question(
    message: str,
    trace_calls: list[dict[str, Any]],
) -> str | None:
    """Return a deterministic adjustment question when inspected supply is short."""
    observations = [
        call.get("result")
        for call in trace_calls
        if call.get("tool_name") == "inspect_question_bank"
        and isinstance(call.get("result"), dict)
        and call["result"].get("ok") is True
    ]
    if not observations:
        return None

    supply = observations[-1]
    total_available = int(supply.get("total_questions") or 0)
    requested_total = _explicit_generation_count(message)
    shortages: list[str] = []
    if requested_total is not None and total_available < requested_total:
        shortages.append(f"总题量需要{requested_total}道，当前只有{total_available}道")

    type_supply = supply.get("question_type_counts") or {}
    for question_type, required in _explicit_question_type_counts(message).items():
        available = int(type_supply.get(question_type) or 0)
        if available < required:
            shortages.append(f"{question_type}需要{required}道，当前只有{available}道")

    if explicit_generation_missing_count(message):
        return "请确认需要生成多少道指定题型的题目。"
    if not shortages and total_available > 0:
        return None
    detail = "；".join(shortages) if shortages else "当前范围内没有可用题目"
    return f"{detail}。请确认是调整题量、题型或范围，还是等待补充题库。"


def _explicit_preserve_knowledge_points_requested(message: str) -> bool:
    return _PRESERVE_KNOWLEDGE_POINTS_PATTERN.search(message) is not None


def _matches_any(message: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(message) is not None for pattern in patterns)


def _apply_explicit_opt_in_guards(
    *, tool_name: str, arguments: dict[str, Any], message: str,
) -> dict[str, Any]:
    """Remove executable constraints that have no explicit teacher provenance."""
    updated = dict(arguments)
    if (
        tool_name == "read_paper"
        and ResponsePolicy.question_type_summary_query(message) is not None
    ):
        # Counts come from one authoritative whole-version observation, never
        # by probing section-local addresses until one fails.
        updated.pop("positions", None)
        updated.pop("addresses", None)

    if tool_name == "prepare_generation_plan":
        explicit_count = _explicit_generation_count(message)
        explicit_score = _explicit_generation_score(message)
        provenance = dict(updated.get("constraint_provenance") or {})

        def mark_teacher_explicit(field: str) -> None:
            provenance[field] = {
                "source": "teacher_message",
                "teacher_explicit": True,
                "strength": "hard",
            }

        if explicit_count is not None:
            updated["question_count"] = explicit_count
            mark_teacher_explicit("question_count")
        if explicit_score is not None:
            updated["total_score"] = explicit_score
            mark_teacher_explicit("total_score")
        if "total_score" in updated and not (
            explicit_score is not None or _matches_any(message, _TOTAL_SCORE_PATTERNS)
        ):
            updated.pop("total_score", None)
        if "question_count" in updated and not (
            explicit_count is not None or _matches_any(message, _QUESTION_COUNT_PATTERNS)
        ):
            updated.pop("question_count", None)
        explicit_type_counts = _explicit_question_type_counts(message)
        if (
            explicit_count is not None
            and explicit_type_counts
            and sum(explicit_type_counts.values()) == explicit_count
        ):
            existing = {
                item.get("question_type"): item
                for item in updated.get("question_type_requirements") or []
                if isinstance(item, dict)
            }
            updated["question_type_requirements"] = [
                {
                    **{
                        key: value
                        for key, value in existing.get(question_type, {}).items()
                        if key == "score_each" and "每题" in message
                    },
                    "question_type": question_type,
                    "count": count,
                }
                for question_type, count in explicit_type_counts.items()
            ]
            updated.pop("question_type_patches", None)
            mark_teacher_explicit("question_type_requirements")
        elif explicit_type_counts:
            # Retain model arguments so the mismatch guard can validate compact
            # aliases such as “选择4、解答2”. Provenance is attached only when the
            # teacher also states the whole-paper count, preserving patch semantics.
            pass
        elif not _ALL_QUESTION_TYPE_PATTERN.search(message):
            updated.pop("question_type_requirements", None)
            updated.pop("question_type_patches", None)
        if provenance:
            updated["constraint_provenance"] = provenance

    if tool_name in {"create_teaching_design", "revise_teaching_design"}:
        container_key = "content" if tool_name == "create_teaching_design" else "patch"
        container = updated.get(container_key)
        if isinstance(container, dict):
            lesson_match = _EXPLICIT_LESSON_COUNT_PATTERN.search(message)
            if lesson_match is not None:
                container = {
                    **container,
                    "lesson_count": _count_value(lesson_match.group("count")),
                }
                updated[container_key] = container
            assessment = container.get("assessment_plan")
            if (
                isinstance(assessment, dict)
                and _EXPLICIT_ASSESSMENT_SCORE_PATTERN.search(message) is None
            ):
                requirements = assessment.get("question_type_requirements")
                if isinstance(requirements, list):
                    sanitized = {
                        **assessment,
                        "question_type_requirements": [
                            {key: value for key, value in item.items() if key != "score_each"}
                            if isinstance(item, dict) else item
                            for item in requirements
                        ],
                    }
                    updated[container_key] = {**container, "assessment_plan": sanitized}

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
