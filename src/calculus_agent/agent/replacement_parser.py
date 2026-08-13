"""Narrow parser for single-question difficulty replacement requests."""

import re

from .schemas import ReplacementIntent


_CHINESE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _number(value: str) -> int | None:
    if value.lstrip("-").isdigit():
        return int(value)
    if value in _CHINESE_NUMBERS:
        return _CHINESE_NUMBERS[value]
    if len(value) == 2 and value[0] == "十" and value[1] in _CHINESE_NUMBERS:
        return 10 + _CHINESE_NUMBERS[value[1]]
    if len(value) == 2 and value[1] == "十" and value[0] in _CHINESE_NUMBERS:
        return _CHINESE_NUMBERS[value[0]] * 10
    if len(value) == 3 and value[1] == "十" and value[0] in _CHINESE_NUMBERS and value[2] in _CHINESE_NUMBERS:
        return _CHINESE_NUMBERS[value[0]] * 10 + _CHINESE_NUMBERS[value[2]]
    return None


def parse_replacement_intent(
    message: str, *, default_difficulty_direction: str | None = None
) -> ReplacementIntent:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("invalid_replacement_intent")
    direction = "easier" if re.search(r"简单|容易", message) else "harder" if re.search(r"难", message) else default_difficulty_direction
    if direction is None:
        raise ValueError("invalid_replacement_intent")
    match = re.search(r"第\s*(-?\d+|[一二三四五六七八九十]+)\s*题", message)
    if not match:
        return ReplacementIntent(
            target_position=1,
            difficulty_direction=direction,
            need_clarification=True,
            clarification_questions=["请明确需要替换的题号，例如第3题。"],
        )
    raw = match.group(1)
    position = _number(raw)
    if position is None or position <= 0:
        raise ValueError("invalid_replacement_intent")
    return ReplacementIntent(target_position=position, difficulty_direction=direction)
