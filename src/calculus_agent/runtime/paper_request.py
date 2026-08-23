"""Deterministic Paper request parsing and Tool argument hints."""

import re
from typing import Any

from calculus_agent.papers.addressing import QuestionAddress

def _paper_read_messages(
    *,
    message: str,
    serialized_context: str,
    requested_positions: list[int] | None = None,
    requested_addresses: list[QuestionAddress] | None = None,
    current_version_id: str | None = None,
    retry: bool = False,
) -> list[dict[str, str]]:
    if requested_addresses:
        payload = [
            address.model_dump(mode="json")
            for address in requested_addresses
        ]
        scope_instruction = (
            f"教师明确指定题型内地址 {payload}；必须调用 "
            f"read_paper(addresses={payload})，不得转换成全卷 position。"
        )
    elif requested_positions:
        scope_instruction = (
            f"教师明确指定全卷内部题号 {requested_positions}；必须调用 "
            f"read_paper(positions={requested_positions})。"
        )
    else:
        scope_instruction = (
            "教师询问整卷情况；调用 read_paper 时省略 addresses 和 positions。"
        )
    return [
        {
            "role": "system",
            "content": (
                "<paper_read_required version_id=\""
                + str(current_version_id or "")
                + "\">教师当前请求依赖当前 Paper，必须立即调用 read_paper 获取当前版本事实。"
                + "</paper_read_required>"
                + scope_instruction
                + "不得凭聊天历史回答，不得在没有 Tool Observation 时输出事实。"
                + ("上一条没有调用读取工具，这次必须调用。" if retry else "")
            ),
        },
        {
            "role": "user",
            "content": (
                message
                + "\n\n<current_workspace_state>"
                + serialized_context
                + "</current_workspace_state>"
            ),
        },
    ]


_QUESTION_NUMBER_VALUE = r"\d+|[一二三四五六七八九十]+"
_SECTION_QUESTION_PATTERN = re.compile(
    rf"(?P<section>选择题|多选题|填空题|计算题|证明题)\s*"
    rf"第\s*(?P<number>{_QUESTION_NUMBER_VALUE})\s*题"
)
_REVERSED_SECTION_QUESTION_PATTERN = re.compile(
    rf"第\s*(?P<number>{_QUESTION_NUMBER_VALUE})\s*题"
    rf"[^。！？,，；;]{{0,12}}?"
    rf"(?P<section>选择题|多选题|填空题|计算题|证明题)"
)
_GLOBAL_QUESTION_PATTERN = re.compile(
    rf"(?:全卷|全卷的)\s*第\s*(?P<number>{_QUESTION_NUMBER_VALUE})\s*题"
)
_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_KNOWLEDGE_CONSTRAINT_TARGET = r"(?:知识点|考点)"
_KNOWLEDGE_PRESERVE_ACTION = (
    r"(?:保持|保留|不变|别变|不要变|"
    r"不动|别动|不要动|"
    r"不改|别改|不要改|"
    r"不调整|别调整|不要调整)"
)
_KNOWLEDGE_CONSTRAINT_GAP = r"[^。！？,，；;]{0,6}"

_PRESERVE_KNOWLEDGE_POINTS_PATTERN = re.compile(
    rf"{_KNOWLEDGE_CONSTRAINT_TARGET}"
    rf"{_KNOWLEDGE_CONSTRAINT_GAP}"
    rf"{_KNOWLEDGE_PRESERVE_ACTION}"
    rf"|{_KNOWLEDGE_PRESERVE_ACTION}"
    rf"{_KNOWLEDGE_CONSTRAINT_GAP}"
    rf"(?:原)?{_KNOWLEDGE_CONSTRAINT_TARGET}"
)


_TOTAL_SCORE_PATTERNS = (
    # "总分100" / "满分 100 分" / "总分保持90分"
    re.compile(
        r"(?:总分|满分|卷面(?:总)?分)"
        r"[^。！？,，；;]{0,8}?"
        r"\d{1,3}\s*分?"
    ),
    # "100分的试卷" / "100分制考试"
    re.compile(
        r"\d{1,3}\s*分(?:制|的)?\s*(?:试卷|卷子|测试|考试)"
    ),
    # Follow-up shorthand such as "90分就可以" / "90即可".
    re.compile(
        r"^\s*\d{1,3}\s*分?\s*(?:就可以|即可|可以|就行|行)?[。！？]?\s*$"
    ),
)


def _question_position(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        tens_value = _CHINESE_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CHINESE_DIGITS.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    return _CHINESE_DIGITS.get(value)


def _explicit_question_addresses(message: str) -> list[QuestionAddress]:
    """Extract only high-confidence section-local address hints.

    This parser is a positive fast path, not a semantic gate. If it cannot
    understand a teacher's wording, the original message still goes to the
    LLM + active Skill for semantic resolution.
    """
    addresses: list[QuestionAddress] = []
    seen: set[tuple[str, int]] = set()

    for pattern in (
        _SECTION_QUESTION_PATTERN,
        _REVERSED_SECTION_QUESTION_PATTERN,
    ):
        for match in pattern.finditer(message):
            section_order = _question_position(match.group("number"))
            if not section_order:
                continue

            address = QuestionAddress(
                section_type=match.group("section"),
                section_order=section_order,
            )
            key = (address.section_type, address.section_order)

            if key not in seen:
                addresses.append(address)
                seen.add(key)

    return addresses


def _explicit_question_positions(message: str) -> list[int]:
    """Extract only explicit legacy references such as 全卷第5题."""
    positions: list[int] = []

    for match in _GLOBAL_QUESTION_PATTERN.finditer(message):
        position = _question_position(match.group("number"))
        if position and position not in positions:
            positions.append(position)

    return positions


def _apply_question_reference_hints(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    addresses: list[QuestionAddress],
    positions: list[int],
) -> dict[str, Any]:
    """Fill only missing references from deterministic positive hints.

    Hints never decide semantic intent. For paper changes, only a single
    unresolved target may be filled from a single high-confidence address.
    """
    updated = dict(arguments)

    if tool_name == "read_paper":
        if not updated.get("addresses") and not updated.get("positions"):
            if addresses:
                updated["addresses"] = [
                    address.model_dump(mode="json")
                    for address in addresses
                ]
            elif positions:
                updated["positions"] = list(positions)
        return updated

    if tool_name != "preview_paper_changes" or len(addresses) != 1:
        return updated

    raw_operations = updated.get("operations")
    if not isinstance(raw_operations, list):
        return updated

    operations = [
        dict(operation) if isinstance(operation, dict) else operation
        for operation in raw_operations
    ]
    target_types = {
        "replace_question",
        "remove_question",
        "change_question_score",
    }
    unresolved = [
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, dict)
        and operation.get("type") in target_types
        and operation.get("target") is None
    ]
    if len(unresolved) != 1:
        return updated

    index = unresolved[0]
    operations[index]["target"] = addresses[0].model_dump(mode="json")
    updated["operations"] = operations
    return updated

