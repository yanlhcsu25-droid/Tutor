from datetime import datetime
from math import isclose
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from calculus_agent.question_types import ALLOWED_QUESTION_TYPES, canonical_question_type


class CurriculumImportRequest(BaseModel):
    directory_text: str = Field(min_length=1, max_length=20000)


class CurriculumNodeRead(BaseModel):
    id: str
    parent_id: str | None
    node_type: str
    code: str | None
    title: str
    sort_order: int


class KnowledgeNodeCreate(BaseModel):
    node_type: Literal["concept", "problem_type", "method"]
    name: str = Field(min_length=1, max_length=255)
    curriculum_node_id: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    review_status: Literal["proposed", "approved"] = "approved"


class KnowledgeNodeRead(BaseModel):
    id: str
    node_type: str
    name: str
    curriculum_node_id: str | None
    review_status: str
    score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)


class DatasetImportRequest(BaseModel):
    path: str
    variants: list[int] = Field(default_factory=lambda: [1], min_length=1)
    limit: int | None = Field(default=None, ge=1, le=10000)


class MMMathImportRequest(BaseModel):
    path: str
    image_root: str | None = None
    limit: int | None = Field(default=None, ge=1, le=10000)
    publish: bool = True


class CMMMathImportRequest(BaseModel):
    path: str
    levels: list[str] = Field(default_factory=lambda: ["七年级", "八年级", "九年级"])
    image_root: str | None = None
    text_only: bool = True
    require_analysis: bool = True
    limit: int | None = Field(default=None, ge=1, le=50000)
    publish: bool = False


class DatasetImportSummary(BaseModel):
    created: int
    existing: int
    skipped: int


class SolverResult(BaseModel):
    solution_steps: list[str] = Field(default_factory=list)
    final_answer: str
    used_knowledge: list[str] = Field(default_factory=list)
    used_methods: list[str] = Field(default_factory=list)
    model_name: str


class VerificationResult(BaseModel):
    status: Literal["verified", "conflict", "unsupported", "error"]
    method: str
    expected: list[str]
    actual: str
    details: list[str] = Field(default_factory=list)


class ClassificationCandidate(BaseModel):
    knowledge_node_id: str
    name: str
    node_type: str
    score: float
    evidence: list[str] = Field(default_factory=list)


class DraftProcessRead(BaseModel):
    draft_id: str
    status: str
    solution: SolverResult
    verification: VerificationResult
    candidates: list[ClassificationCandidate]


class DraftApproveRequest(BaseModel):
    primary_concept_id: str
    secondary_concept_ids: list[str] = Field(default_factory=list)
    problem_type_ids: list[str] = Field(default_factory=list)
    method_ids: list[str] = Field(default_factory=list)


class QuestionRead(BaseModel):
    id: str
    draft_id: str
    question_text: str
    final_answer: str | None
    verification_status: str
    knowledge: list[KnowledgeNodeRead]


class KnowledgeQuota(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1, le=100)


class SectionRequirement(BaseModel):
    question_type: str
    count: int = Field(ge=1, le=100)
    score_per_question: float = Field(gt=0, le=300)
    total_score: float = Field(gt=0, le=300)

    @model_validator(mode="before")
    @classmethod
    def normalize_count_key(cls, value):
        if isinstance(value, dict) and "count" not in value and "n" in value:
            return {**value, "count": value["n"]}
        return value

    @field_validator("question_type", mode="before")
    @classmethod
    def normalize_question_type(cls, value: str) -> str:
        return canonical_question_type(str(value))

    @model_validator(mode="after")
    def validate_section(self):
        if self.question_type not in ALLOWED_QUESTION_TYPES:
            raise ValueError("不支持的题型")
        if not isclose(self.count * self.score_per_question, self.total_score):
            raise ValueError("部分总分必须等于题数乘以每题分值")
        return self


class PaperBlueprint(BaseModel):
    title: str = Field(default="高等数学测试卷", min_length=1, max_length=100)
    total_questions: int = Field(ge=1, le=100)
    total_score: int = Field(default=100, ge=1, le=300)
    sections: list[SectionRequirement] = Field(default_factory=list)
    question_type_counts: dict[str, int] = Field(default_factory=dict)
    knowledge_quotas: list[KnowledgeQuota] = Field(default_factory=list)
    soft_knowledge_preferences: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    image_question_count: int = Field(default=0, ge=0, le=100)
    strict_knowledge: bool = False
    locked_question_ids: list[str] = Field(default_factory=list)
    manual_question_ids: list[str] = Field(default_factory=list)
    excluded_question_ids: list[str] = Field(default_factory=list)
    question_order: list[str] = Field(default_factory=list)
    score_overrides: dict[str, float] = Field(default_factory=dict)
    seed: int = 42

    @model_validator(mode="before")
    @classmethod
    def derive_raw_section_summaries(cls, value):
        """Repair model-produced stale summary fields before scalar validation."""
        if not isinstance(value, dict) or not isinstance(value.get("sections"), list):
            return value
        if not value["sections"] or not all(isinstance(raw, dict) for raw in value["sections"]):
            return value
        sections = []
        for raw in value["sections"]:
            if not isinstance(raw, dict):
                sections.append(raw)
                continue
            section = dict(raw)
            if "count" not in section and "n" in section:
                section["count"] = section["n"]
            sections.append(section)
        counts = [section.get("count") for section in sections if isinstance(section, dict)]
        totals = [section.get("total_score") for section in sections if isinstance(section, dict)]
        if sections and all(isinstance(count, int) and count > 0 for count in counts):
            value = {**value, "sections": sections, "total_questions": sum(counts)}
            if all(isinstance(score, (int, float)) and score > 0 for score in totals):
                value["total_score"] = round(sum(totals))
        return value

    @field_validator("question_type_counts", mode="before")
    @classmethod
    def normalize_question_type_counts(cls, value):
        return {
            canonical_question_type(str(question_type)): count
            for question_type, count in (value or {}).items()
        }

    @model_validator(mode="after")
    def validate_constraints(self):
        if any(value < 0 for value in self.question_type_counts.values()):
            raise ValueError("题型数量不得为负数")
        if any(value not in ALLOWED_QUESTION_TYPES for value in self.question_type_counts):
            raise ValueError("包含不支持的题型")
        if self.sections:
            derived = {item.question_type: item.count for item in self.sections}
            if len(derived) != len(self.sections):
                raise ValueError("同一题型只能配置一个部分")
            self.question_type_counts = derived
            self.total_questions = sum(item.count for item in self.sections)
            section_score = sum(item.total_score for item in self.sections)
            if not isclose(section_score, round(section_score)):
                raise ValueError("各部分总分之和必须为整数")
            self.total_score = round(section_score)
        elif self.question_type_counts and sum(self.question_type_counts.values()) != self.total_questions:
            raise ValueError("题型数量之和必须等于题目总数")
        if self.image_question_count > self.total_questions:
            raise ValueError("图片题数量不能超过题目总数")
        if len(set(self.locked_question_ids)) != len(self.locked_question_ids):
            raise ValueError("锁定题目不能重复")
        if len(self.locked_question_ids) > self.total_questions:
            raise ValueError("锁定题目数量不能超过题目总数")
        required_ids = set(self.locked_question_ids) | set(self.manual_question_ids)
        if len(required_ids) > self.total_questions:
            raise ValueError("锁定和手动添加的题目数量不能超过题目总数")
        overlap = required_ids & set(self.excluded_question_ids)
        if overlap:
            raise ValueError("指定题目不能同时被排除")
        if any(score < 1 or score > self.total_score for score in self.score_overrides.values()):
            raise ValueError("单题分值必须介于1和试卷总分之间")
        if sum(self.score_overrides.values()) > self.total_score:
            raise ValueError("指定题目的分值之和不能超过试卷总分")
        if len(self.score_overrides) > self.total_questions:
            raise ValueError("分值调整题目数量不能超过题目总数")
        minimum_total = sum(self.score_overrides.values()) + (
            self.total_questions - len(self.score_overrides)
        )
        if minimum_total > self.total_score:
            raise ValueError("剩余总分不足以为其他题目分配至少1分")
        return self


class BlueprintCreateRead(BaseModel):
    blueprint_id: str
    status: Literal["draft", "confirmed", "used"]
    blueprint: PaperBlueprint
    cached: bool = False
    agent_message: str | None = None
    needs_clarification: bool = False
    paper_result: dict | None = None


class PaperCreateRequest(BaseModel):
    blueprint_id: str


class ConstraintViolationRead(BaseModel):
    code: str
    field: str
    required: int | float | str | list | dict | None
    actual: int | float | str | list | dict | None
    question_ids: list[str] = Field(default_factory=list)
    repairable: bool
    message: str


class ValidationReportRead(BaseModel):
    id: str
    paper_id: str
    passed: bool
    violations: list[ConstraintViolationRead] = Field(default_factory=list)
    created_at: datetime


class QuestionProfileBase(BaseModel):
    difficulty: int = Field(ge=1, le=5)
    estimated_time_min: int = Field(ge=1, le=180)
    reasoning_depth: int = Field(ge=1, le=5)
    calculation_load: int = Field(ge=1, le=5)
    knowledge_depth: int = Field(ge=1, le=5)
    comprehensive_level: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=2, max_length=1000)


class QuestionProfileCandidate(QuestionProfileBase):
    question_id: str


class QuestionProfileUpdate(BaseModel):
    difficulty: int | None = Field(default=None, ge=1, le=5)
    estimated_time_min: int | None = Field(default=None, ge=1, le=180)
    reasoning_depth: int | None = Field(default=None, ge=1, le=5)
    calculation_load: int | None = Field(default=None, ge=1, le=5)
    knowledge_depth: int | None = Field(default=None, ge=1, le=5)
    comprehensive_level: int | None = Field(default=None, ge=1, le=5)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = Field(default=None, min_length=2, max_length=1000)
    approve: bool = False


class QuestionProfileRead(QuestionProfileBase):
    profile_id: str
    question_id: str
    question_text: str
    question_type: str
    knowledge: list[str] = Field(default_factory=list)
    profile_version: int
    profile_source: Literal["auto", "human", "corrected"]
    profile_status: Literal["pending", "approved", "needs_review"]
    created_at: datetime
    reviewed_at: datetime | None = None


class QuestionProfileBatchRead(BaseModel):
    eligible: int
    created: int
    reused: int
    needs_review: int


class QuestionProfileBatchRequest(BaseModel):
    source_name: str | None = "ocr_import"
    force: bool = False


class SupplyCheckRead(BaseModel):
    feasible: bool
    violations: list[ConstraintViolationRead] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SavedPaperRead(BaseModel):
    paper_id: str
    blueprint_id: str
    root_paper_id: str
    parent_version_id: str | None
    version: int
    status: Literal["draft", "validating", "passed", "failed"]
    total_score: int
    validation_status: str
    preview: "PaperPreviewRead"
    validation_report: ValidationReportRead
    created_at: datetime


class NaturalLanguagePaperRequest(BaseModel):
    requirement: str = Field(min_length=2, max_length=2000)
    base_blueprint_id: str | None = None
    current_paper_id: str | None = None
    conversation_history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class PaperItemRead(BaseModel):
    item_id: str | None = None
    question_id: str
    question_text: str
    question_type: str
    score: float
    knowledge: list[str] = Field(default_factory=list)
    final_answer: str | None = None
    solution_steps: list[str] = Field(default_factory=list)
    has_image: bool = False
    locked: bool = False
    source_name: str | None = None
    source_page: int | None = None
    chapter: str | None = None
    review_status: str = "approved"


class PaperItemUpdate(BaseModel):
    score: float | None = Field(default=None, ge=0.5, le=300)


class PaperReorderRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=100)


class PaperLockRequest(BaseModel):
    locked: bool


class PaperUndoRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class PaperRestoreRequest(BaseModel):
    version_id: str


class PaperOperationRead(BaseModel):
    operation_id: str
    root_paper_id: str
    source_paper_id: str
    result_paper_id: str
    operation_type: str
    operations: list[dict] = Field(default_factory=list)
    undone_operation_id: str | None = None
    created_at: datetime


class MistakePrepCreate(BaseModel):
    question_text: str = Field(min_length=2, max_length=10000)
    final_answer: str = Field(min_length=1, max_length=5000)
    solution_text: str = Field(min_length=2, max_length=20000)
    error_reason: str = Field(min_length=2, max_length=5000)
    question_type: str | None = None
    knowledge_names: list[str] = Field(min_length=1, max_length=20)
    match_count: int = Field(default=5, ge=1, le=20)


class MistakePrepMatchRead(BaseModel):
    question_id: str
    question_text: str
    question_type: str
    final_answer: str | None
    solution_steps: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)


class MistakePrepRead(BaseModel):
    id: str
    question_text: str
    final_answer: str
    solution_text: str
    error_reason: str
    question_type: str | None
    knowledge_names: list[str]
    matches: list[MistakePrepMatchRead]
    created_at: datetime


class VisionQuestionExtractRequest(BaseModel):
    question_image: str = Field(min_length=20, max_length=30_000_000)
    solution_image: str | None = Field(default=None, max_length=30_000_000)


class VisionQuestionExtractRead(BaseModel):
    question_text: str
    options: list[str] = Field(default_factory=list)
    question_type: str
    final_answer: str
    solution_text: str
    knowledge_names: list[str] = Field(default_factory=list)
    needs_review: bool = True
    warnings: list[str] = Field(default_factory=list)


class QuestionOptionRead(BaseModel):
    id: str
    question_text: str
    question_type: str
    knowledge: list[str] = Field(default_factory=list)
    # 来源追溯（仅 OCR 导入题可回查，其余数据来源统一为 None）
    original_number: str | None = None
    source_name: str | None = None
    source_page: int | None = None
    chapter: str | None = None
    knowledge_match_status: str = "current"
    difficulty: int | None = None
    estimated_time_min: int | None = None
    reasoning_depth: int | None = None
    calculation_load: int | None = None
    comprehensive_level: int | None = None


class QuestionDetailRead(BaseModel):
    """题库人工查看层的正式题详情。

    solution_content 为完整参考解答文本块（取自 QuestionDraft.solution_text），
    不拆分「答案」「解析」，也不从 Question.solution_json 重组。
    """

    id: str
    question_text: str
    question_type: str
    knowledge: list[str] = Field(default_factory=list)
    knowledge_node_ids: list[str] = Field(default_factory=list)
    solution_content: str | None = None
    final_answer: str | None = None
    chapter: str | None = None
    original_number: str | None = None
    source_name: str | None = None
    source_page: int | None = None
    difficulty: int | None = None
    knowledge_match_status: str = "current"
    is_active: bool = True


class ConstraintCheck(BaseModel):
    name: str
    required: int | float | str
    actual: int | float | str
    satisfied: bool


class PaperPreviewRead(BaseModel):
    title: str
    total_score: float
    items: list[PaperItemRead]
    constraints: list[ConstraintCheck]
    warnings: list[str] = Field(default_factory=list)
    feasible: bool


class AgentRunRequest(BaseModel):
    request: str = Field(min_length=2, max_length=4000)
    max_steps: int = Field(default=12, ge=1, le=30)
    mode: Literal["single_agent", "multi_agent"] = "multi_agent"


class ToolCallTraceRead(BaseModel):
    step: int
    actor: str
    tool_name: str
    arguments: dict
    result: dict
    status: str
    duration_ms: int


class AgentRunRead(BaseModel):
    run_id: str
    status: str
    mode: str
    final_response: str | None
    steps_used: int
    error_message: str | None = None
    current_paper: PaperPreviewRead | None = None
    traces: list[ToolCallTraceRead] = Field(default_factory=list)
