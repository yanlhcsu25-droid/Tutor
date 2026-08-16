"""Deterministic first-round parser for common Chinese teacher requests."""

import re

from .schemas import RequirementBlueprint, RequirementPreferences


_QUESTION_TYPES = ("选择题", "多选题", "填空题", "计算题", "证明题")


def _scope(text: str) -> list[str]:
    compound = re.search(
        r"第\s*([一二三四五六七八九十百0-9]+)\s*章\s*第\s*([一二三四五六七八九十百0-9]+)\s*节",
        text,
    )
    if compound:
        return [f"第{compound.group(1)}章第{compound.group(2)}节"]
    match = re.search(r"第\s*([一二三四五六七八九十百0-9]+)\s*(章|节)", text)
    if not match:
        return []
    number, unit = match.groups()
    return [f"第{number}{unit}"]


def _difficulty(text: str) -> str:
    if re.search(r"简单|容易|基础", text):
        return "easy"
    if re.search(r"困难|难一些|难一点|提高", text):
        return "hard"
    return "normal"


def _total_score(text: str, paper_type: str) -> int | None:
    if paper_type == "homework":
        return None
    match = re.search(r"(?:满分|总分)\s*[:：]?\s*(\d+)\s*分?", text)
    return int(match.group(1)) if match else 100


def parse_teacher_requirement(requirement: str) -> RequirementBlueprint:
    """Parse a teacher sentence into validated requirement data.

    The function is deliberately deterministic in Phase 2A. It produces no
    question IDs and does not invoke the existing paper composer.
    """
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("requirement must be a non-empty string")
    text = requirement.strip()
    if "期中" in text:
        paper_type = "midterm"
    elif "期末" in text:
        paper_type = "final"
    elif "课后练习" in text or "作业" in text:
        paper_type = "homework"
    else:
        paper_type = "chapter_test"

    scope = _scope(text)
    preferences = RequirementPreferences(
        more_question_types=[kind for kind in _QUESTION_TYPES if re.search(
            rf"多一点[^，。；]*{kind}|{kind}[^，。；]*多一点", text
        )],
    )
    questions: list[str] = []
    if paper_type == "midterm" and not scope:
        questions.append("请确认本次期中考试的知识范围，例如第一章至第三章。")
    if paper_type == "final" and not re.search(r"难度.*(?:比例|占比)|(?:比例|占比).*难度|简单\s*\d+%", text):
        questions.append("请确认本次期末考试的难度占比，例如基础题60%、提高题30%、综合题10%。")
    return RequirementBlueprint(
        paper_type=paper_type,
        scope=scope,
        total_score=_total_score(text, paper_type),
        difficulty=_difficulty(text),
        preferences=preferences,
        need_clarification=bool(questions),
        clarification_questions=questions,
    )
