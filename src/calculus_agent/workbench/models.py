from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QuestionType(StrEnum):
    SELECTION = "selection"
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    FILL_BLANK = "fill_blank"
    CALCULATION = "calculation"
    PROOF = "proof"
    SUBJECTIVE = "subjective"
    SHORT_ANSWER = "short_answer"
    COMPOSITE = "composite"
    OTHER = "other"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    PUBLISHED = "published"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)


class QuestionPayload(BaseModel):
    """OCR 题目校验后的结构化数据。

    核心字段：
    - question_content: 题干 + 选项（统一文本块）
    - solution_content: 参考解答（整段文本，不为空不报 Error）
    - question_type: 题型
    - 子题追踪：parent_original_number, subquestion_label, source_group_id
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(
        pattern=(
            r"^(?:q_[0-9a-f]{32}|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
        )
    )
    source_file_id: str = Field(pattern=r"^src_[0-9a-f]{32}$")
    page_number: int = Field(ge=1)
    original_number: str = Field(min_length=1, max_length=64)
    question_type: QuestionType

    # 核心两个文本块
    question_content: str = Field(min_length=1)
    solution_content: str = ""

    # 旧版兼容字段（从 question_content / solution_content 衍生，不再强制必填）
    stem: str = ""
    options: dict[str, str] = Field(default_factory=dict)
    answer: str = ""                   # 不再 min_length=1
    analysis: str = ""

    # 元数据
    chapter: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    review_notes: str = ""

    # 子题追踪
    parent_original_number: str | None = None
    subquestion_label: str | None = None
    source_group_id: str | None = None

    # 来源
    source_bbox: BoundingBox | None = None
    ocr_markdown: str
    edited_markdown: str

    # ── validators ──

    @field_validator("original_number")
    @classmethod
    def validate_original_number(cls, value: str) -> str:
        clean = value.strip()
        # 一级题号允许 1、1.2、1-2；自动拆分的子题使用 1(2) 形式。
        # 切题器与审核校验必须接受同一套题号表示，避免子题在审核阶段
        # 被误判为非法原始题号。
        if not re.fullmatch(
            r"[0-9一二三四五六七八九十百]+"
            r"(?:(?:[.．_\-][0-9一二三四五六七八九十百]+)|"
            r"\([0-9一二三四五六七八九十百]+\))*",
            clean,
        ):
            raise ValueError("原始题号只能包含数字、中文数字和层级分隔符")
        return clean

    @field_validator("knowledge_points")
    @classmethod
    def validate_knowledge_points(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            point = item.strip()
            if not point:
                raise ValueError("知识点不能包含空项")
            if len(point) > 80:
                raise ValueError("单个知识点不能超过80个字符")
            if any(char in point for char in "\n\r\t"):
                raise ValueError("知识点不能包含换行或制表符")
            if point not in seen:
                seen.add(point)
                cleaned.append(point)
        return cleaned

    @field_validator("options")
    @classmethod
    def validate_option_keys(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, text in value.items():
            option_key = key.strip().upper()
            if not re.fullmatch(r"[A-H]", option_key):
                raise ValueError(f"非法选项标识：{key}")
            if not text.strip():
                raise ValueError(f"选项 {option_key} 不能为空")
            cleaned[option_key] = text.strip()
        return cleaned

    @model_validator(mode="after")
    def validate_choices(self) -> "QuestionPayload":
        # 选择题才校验选项
        if self.question_type in {
            QuestionType.SELECTION, QuestionType.SINGLE_CHOICE, QuestionType.MULTIPLE_CHOICE
        }:
            if len(self.options) < 2:
                raise ValueError("选择题至少需要两个合法选项")
            # 只校验具有明确答案语义的选项引用。answer 在新版审核结构中
            # 可能承载整段“参考解答”，绝不能扫描公式中的 f/e/frac 等字母。
            if self.answer.strip():
                answers = extract_explicit_choice_answers(self.answer)
                missing = answers - set(self.options)
                if missing:
                    raise ValueError(f"答案引用了不存在的选项：{', '.join(sorted(missing))}")
        return self


_CHOICE_SEQUENCE = r"[A-H](?:\s*[,，、/]\s*[A-H])*"
_EXPLICIT_CHOICE_PATTERNS = (
    re.compile(rf"(?i)(?:答案)\s*[:：]?\s*[（(]?\s*(?P<choices>{_CHOICE_SEQUENCE})"),
    re.compile(rf"(?i)(?:应选|故选|选择)\s*[（(]?\s*(?P<choices>{_CHOICE_SEQUENCE})"),
)
_DIRECT_CHOICE_ANSWER_RE = re.compile(rf"(?i)^\s*(?P<choices>{_CHOICE_SEQUENCE})\s*$")


def extract_explicit_choice_answers(answer: str) -> set[str]:
    """Extract choices only from an answer field or explicit answer expressions."""
    direct = _DIRECT_CHOICE_ANSWER_RE.fullmatch(answer)
    matches = [direct] if direct else [
        match for pattern in _EXPLICIT_CHOICE_PATTERNS for match in pattern.finditer(answer)
    ]
    choices: set[str] = set()
    for match in matches:
        if match is not None:
            choices.update(re.findall(r"[A-H]", match.group("choices").upper()))
    return choices


class MarkdownValidationIssue(BaseModel):
    field: str
    message: str
    line: int | None = None


class ValidationResult(BaseModel):
    valid: bool
    issues: list[MarkdownValidationIssue] = Field(default_factory=list)
    warnings: list[MarkdownValidationIssue] = Field(default_factory=list)
    parsed: dict[str, Any] | None = None
