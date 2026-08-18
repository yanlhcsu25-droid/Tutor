import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from calculus_agent.db import Base
from calculus_agent.question_types import canonical_question_type


def new_id() -> str:
    return str(uuid.uuid4())


class CurriculumNode(Base):
    __tablename__ = "curriculum_node"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    textbook_id: Mapped[str | None] = mapped_column(
        ForeignKey("textbook.id"), nullable=True, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("curriculum_node.id"), nullable=True, index=True
    )
    node_type: Mapped[str] = mapped_column(String(30), index=True)
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    sort_order: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(80), default="teacher_directory")
    review_status: Mapped[str] = mapped_column(String(30), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Textbook(Base):
    __tablename__ = "textbook"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), index=True)
    edition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class KnowledgeNode(Base):
    __tablename__ = "knowledge_node"
    __table_args__ = (UniqueConstraint("node_type", "normalized_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    curriculum_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("curriculum_node.id"), nullable=True, index=True
    )
    node_type: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), default="directory")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    review_status: Mapped[str] = mapped_column(String(30), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class KnowledgeAlias(Base):
    __tablename__ = "knowledge_alias"
    __table_args__ = (UniqueConstraint("node_id", "normalized_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_node.id"), index=True)
    alias: Mapped[str] = mapped_column(String(255), index=True)
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)


class QuestionDraft(Base):
    __tablename__ = "question_draft"
    __table_args__ = (UniqueConstraint("source_name", "source_item_id", "variant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_name: Mapped[str] = mapped_column(String(80), index=True)
    source_item_id: Mapped[str] = mapped_column(String(120), index=True)
    variant: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    language: Mapped[str] = mapped_column(String(20), default="zh-CN", index=True)
    grade: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    question_type: Mapped[str] = mapped_column(String(40), default="计算题", index=True)
    source_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_subtopic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question_text: Mapped[str] = mapped_column(Text)
    reference_answers_json: Mapped[list] = mapped_column(JSON, default=list)
    answer_types_json: Mapped[list] = mapped_column(JSON, default=list)
    options_json: Mapped[list] = mapped_column(JSON, default=list)
    solution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    keywords_json: Mapped[list] = mapped_column(JSON, default=list)
    normalized_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    proposed_classification_json: Mapped[dict] = mapped_column(JSON, default=dict)
    solver_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    @validates("question_type")
    def _validate_question_type(self, key, value):
        return canonical_question_type(value)


class Question(Base):
    __tablename__ = "question"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str] = mapped_column(ForeignKey("question_draft.id"), unique=True, index=True)
    # Materialized owning chapter derived from current semantic knowledge
    # links + curriculum taxonomy. Knowledge-write services keep it synchronized.
    curriculum_chapter_id: Mapped[str | None] = mapped_column(
        ForeignKey("curriculum_node.id"), nullable=True, index=True
    )
    question_text: Mapped[str] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    question_type: Mapped[str] = mapped_column(String(40), default="计算题", index=True)
    default_score: Mapped[int] = mapped_column(Integer, default=10)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution_json: Mapped[dict] = mapped_column(JSON, default=dict)
    verification_status: Mapped[str] = mapped_column(String(30), index=True)
    review_status: Mapped[str] = mapped_column(String(30), default="approved", index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    knowledge_match_status: Mapped[str] = mapped_column(
        String(30), default="current", index=True
    )
    publish_source: Mapped[str] = mapped_column(
        String(30), default="manual", index=True
    )
    ai_review_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_sample_required: Mapped[bool] = mapped_column(
        default=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @validates("question_type")
    def _validate_question_type(self, key, value):
        return canonical_question_type(value)


class QuestionKnowledgeLink(Base):
    __tablename__ = "question_knowledge_link"
    __table_args__ = (UniqueConstraint("question_id", "knowledge_node_id", "relation_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    question_id: Mapped[str] = mapped_column(ForeignKey("question.id"), index=True)
    knowledge_node_id: Mapped[str] = mapped_column(ForeignKey("knowledge_node.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(40), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)


class QuestionKnowledgeReview(Base):
    __tablename__ = "question_knowledge_review"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    question_id: Mapped[str] = mapped_column(ForeignKey("question.id"), index=True)
    ai_prediction_json: Mapped[list] = mapped_column(JSON, default=list)
    human_final_json: Mapped[list] = mapped_column(JSON, default=list)
    deleted_by_human_json: Mapped[list] = mapped_column(JSON, default=list)
    added_by_human_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class QuestionProfile(Base):
    __tablename__ = "question_profile"
    __table_args__ = (UniqueConstraint("question_id", "profile_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    question_id: Mapped[str] = mapped_column(ForeignKey("question.id"), index=True)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    difficulty: Mapped[int] = mapped_column(Integer, index=True)
    estimated_time_min: Mapped[int] = mapped_column(Integer, index=True)
    reasoning_depth: Mapped[int] = mapped_column(Integer, index=True)
    calculation_load: Mapped[int] = mapped_column(Integer, index=True)
    knowledge_depth: Mapped[int] = mapped_column(Integer, index=True)
    comprehensive_level: Mapped[int] = mapped_column(Integer, index=True)
    confidence: Mapped[float] = mapped_column(Float)
    profile_source: Mapped[str] = mapped_column(String(30), index=True)
    profile_status: Mapped[str] = mapped_column(String(30), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaperBlueprintRecord(Base):
    __tablename__ = "paper_blueprint"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255), index=True)
    blueprint_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RequirementParseCache(Base):
    __tablename__ = "requirement_parse_cache"

    requirement_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    requirement_text: Mapped[str] = mapped_column(Text)
    blueprint_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class Paper(Base):
    __tablename__ = "paper"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    blueprint_id: Mapped[str] = mapped_column(ForeignKey("paper_blueprint.id"), index=True)
    root_paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper.id"), nullable=True, index=True
    )
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper.id"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    title: Mapped[str] = mapped_column(String(255))
    total_score: Mapped[int] = mapped_column(Integer)
    teaching_design_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("teaching_design_version.id"),
        nullable=True,
        index=True,
    )
    validation_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class PaperItem(Base):
    __tablename__ = "paper_item"
    __table_args__ = (
        UniqueConstraint("paper_id", "question_id"),
        UniqueConstraint("paper_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("question.id"), index=True)
    section: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    locked: Mapped[bool] = mapped_column(default=False)


class PaperOperationHistory(Base):
    __tablename__ = "paper_operation_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    root_paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    source_paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    result_paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), unique=True, index=True)
    operation_type: Mapped[str] = mapped_column(String(50), index=True)
    operations_json: Mapped[list] = mapped_column(JSON, default=list)
    before_state_json: Mapped[dict] = mapped_column(JSON)
    after_state_json: Mapped[dict] = mapped_column(JSON)
    undone_operation_id: Mapped[str | None] = mapped_column(
        ForeignKey("paper_operation_history.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class AgentPendingReplacement(Base):
    """One confirmation-gated replacement per explicitly identified conversation."""

    __tablename__ = "agent_pending_replacement"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class AgentPendingGeneration(Base):
    """Validated generation requirements awaiting explicit teacher confirmation."""

    __tablename__ = "agent_pending_generation"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class AgentWorkingMemoryRecord(Base):
    __tablename__ = "agent_working_memory"
    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TeacherAgentConversationMessage(Base):
    """Short, persisted conversational context for a teacher-agent session."""

    __tablename__ = "teacher_agent_conversation_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(String(120), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class TeacherAgentRunTrace(Base):
    """One autonomous Agent turn, including tool observations and final response."""

    __tablename__ = "teacher_agent_run_trace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, unique=True, index=True, default=new_id
    )
    conversation_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    paper_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_message: Mapped[str] = mapped_column(Text)
    # ── Run-level lifecycle (source of truth for one user turn) ──
    status: Mapped[str] = mapped_column(String(40), default="received", index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_name: Mapped[str] = mapped_column(String(60), default="teacher_agent")
    tool_calls_json: Mapped[list] = mapped_column(JSON, default=list)
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_status: Mapped[str] = mapped_column(String(40), index=True)
    state_before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state_after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(60), nullable=True)


class TeacherAgentSpan(Base):
    """One observable step inside a Teacher Agent run.

    Spans form a tree via ``parent_span_id`` so a run can be reconstructed as:

        agent (teacher_agent)
        ├── model_call
        ├── tool_call
        │   └── state_transition
        └── tool_call
            └── state_transition

    ``run_id`` is the correlation id that links every span back to its
    :class:`TeacherAgentRunTrace`. The table is the local source of truth;
    Langfuse (if configured) is a separate, optional observability backend.
    """

    __tablename__ = "teacher_agent_span"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    span_id: Mapped[str] = mapped_column(
        String(36), unique=True, index=True, default=new_id
    )
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    span_type: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AdjustmentPlanRecord(Base):
    """Read-only preview plan.  Phase 2C-2 deliberately never applies it."""

    __tablename__ = "adjustment_plan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    base_paper_version_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    operations_json: Mapped[list] = mapped_column(JSON, default=list)
    before_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    satisfied_constraints_json: Mapped[list] = mapped_column(JSON, default=list)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    blocking_errors_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    applied_version_id: Mapped[str | None] = mapped_column(ForeignKey("paper.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class AgentPendingAdjustment(Base):
    __tablename__ = "agent_pending_adjustment"

    conversation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("adjustment_plan.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class ValidationReport(Base):
    __tablename__ = "validation_report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("paper.id"), index=True)
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class ConstraintViolation(Base):
    __tablename__ = "constraint_violation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_id: Mapped[str] = mapped_column(ForeignKey("validation_report.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    field: Mapped[str] = mapped_column(String(255))
    required_json: Mapped[object] = mapped_column(JSON)
    actual_json: Mapped[object] = mapped_column(JSON)
    question_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    repairable: Mapped[bool] = mapped_column(default=True)
    message: Mapped[str] = mapped_column(Text)


class MistakePrepTask(Base):
    __tablename__ = "mistake_prep_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    grade: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    question_text: Mapped[str] = mapped_column(Text)
    final_answer: Mapped[str] = mapped_column(Text)
    solution_text: Mapped[str] = mapped_column(Text)
    error_reason: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    knowledge_names_json: Mapped[list] = mapped_column(JSON, default=list)
    matched_question_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class AgentRun(Base):
    __tablename__ = "agent_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_request: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(30), default="multi_agent", index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolCallTrace(Base):
    __tablename__ = "tool_call_trace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(80), index=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


# ── OCR 导入管线（上游，不是平行题库） ──

class OcrImportSource(Base):
    """OCR 导入的 PDF 来源文件。"""
    __tablename__ = "ocr_import_source"
    __table_args__ = (UniqueConstraint("sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_name: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(Text)
    # ``sha256`` remains the unique import fingerprint for backward-compatible
    # SQLite schemas. ``content_sha256`` stores the actual file digest.
    sha256: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Source 级导入布局；旧记录允许为空，由 workbench 明确回退为 inline。
    layout_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OcrImportDraft(Base):
    """OCR 切题后的审校草稿。发布后转换到 QuestionDraft → Question。"""
    __tablename__ = "ocr_import_draft"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("ocr_import_source.id"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    original_number: Mapped[str] = mapped_column(String(40))
    ocr_markdown: Mapped[str] = mapped_column(Text)
    edited_markdown: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    bbox_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    knowledge_points_json: Mapped[list] = mapped_column(JSON, default=list)
    # AI 预标注的只读快照与人工核对结果。它只服务 Shadow Evaluation，
    # 正式发布仍以 knowledge_points_json（人工确认）为唯一事实来源。
    knowledge_shadow_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    difficulty_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    formal_question_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    revision_of_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    content_confirmed: Mapped[bool] = mapped_column(default=False, index=True)
    # 仅用于 OCR 候选/待审核阶段提示题目-答案匹配情况；正式题库不展示、不筛选该字段。
    match_status: Mapped[str] = mapped_column(String(20), default="matched", index=True)
    match_method: Mapped[str] = mapped_column(String(20), default="inline")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_review_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    publish_source: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    quality_sample_required: Mapped[bool] = mapped_column(
        default=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OcrPage(Base):
    """OCR 导入的整页 Markdown（切题的输入快照）。

    raw_markdown    首次 OCR 产出的整页原文，永不被覆盖。
    edited_markdown 人工修正后的整页原文，初始等于 raw_markdown；
                    「重新识别题目」始终以 edited_markdown 作为切题输入。
    """

    __tablename__ = "ocr_page"
    __table_args__ = (UniqueConstraint("source_id", "page_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("ocr_import_source.id"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    raw_markdown: Mapped[str] = mapped_column(Text)
    edited_markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OcrTask(Base):
    """单次 OCR 识别任务（一张图片或多页 PDF）。"""

    __tablename__ = "ocr_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_filename: Mapped[str] = mapped_column(String(500))
    image_path: Mapped[str] = mapped_column(Text)
    page_images_json: Mapped[list] = mapped_column(JSON, default=list)
    engine: Mapped[str] = mapped_column(String(40), default="paddleocr")
    engine_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class OcrBlock(Base):
    """OCR 识别出的单个文本/公式块，支持人工审核订正。"""

    __tablename__ = "ocr_block"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ocr_task.id"), index=True)
    block_order: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    block_type: Mapped[str] = mapped_column(String(20), default="text")
    bbox_x: Mapped[float] = mapped_column(Float)
    bbox_y: Mapped[float] = mapped_column(Float)
    bbox_w: Mapped[float] = mapped_column(Float)
    bbox_h: Mapped[float] = mapped_column(Float)
    original_text: Mapped[str] = mapped_column(Text)
    original_latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    merged_question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
