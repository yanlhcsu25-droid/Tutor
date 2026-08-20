from collections.abc import Iterator
import hashlib
import re
from datetime import UTC, datetime

from calculus_agent.question_types import ALLOWED_QUESTION_TYPES, canonical_question_type
from calculus_agent.questions.chapter_assignment import (
    chapter_display_name,
    sync_question_chapter_ownership,
)
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, exists, false, func, or_, select
from sqlalchemy.orm import Session

from calculus_agent.config import Settings, get_settings
from calculus_agent.datasets.cmm_math import import_cmm_math
from calculus_agent.datasets.mm_math import import_mm_math
from calculus_agent.datasets.ugmathbench import import_ugmathbench
from calculus_agent.db import build_session_factory
from calculus_agent.knowledge.curriculum import (
    import_curriculum,
    retire_directory_knowledge_nodes,
    sync_directory_knowledge_nodes,
)
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.knowledge.retrieval import retrieve_knowledge
from calculus_agent.knowledge.chapter import (
    resolve_question_chapter,
    resolve_questions_chapters,
)
from calculus_agent.workbench.chapter_filter import chapter_filter_knowledge_id_sets
from calculus_agent.knowledge.steward import DraftNotFoundError, process_draft
from calculus_agent.knowledge.classification import (
    confirm_question_knowledge,
    classify_knowledge_points,
    ensure_calculus_taxonomy,
    suggest_question_knowledge,
)
from calculus_agent.models import (
    AgentRun,
    CurriculumNode,
    KnowledgeAlias,
    KnowledgeNode,
    OcrImportDraft,
    OcrImportSource,
    Question,
    QuestionProfile,
    QuestionKnowledgeLink,
    QuestionDraft,
    RequirementParseCache,
    TeacherAgentRunTrace,
    TeacherAgentSpan,
    Textbook,
    ToolCallTrace,
)
from calculus_agent.orchestration.service import run_paper_agent
from calculus_agent.agent.agent import (
    TeacherAgentResult,
    build_teacher_agent_backend,
    run_teacher_agent,
)
from calculus_agent.agent.conversation_state import (
    DatabaseConversationHistoryStore,
    DatabasePendingReplacementStore,
    PendingGenerationStaleError,
)
from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    GenerationPlanPatch,
    GenerationPlanPreview,
    QuestionTypePatch,
)
from calculus_agent.agent.services.generation import (
    GenerationService,
    NoPendingGenerationError,
)
from calculus_agent.agent.state.service import RuntimeStateService
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult
from calculus_agent.agent.trace_log import (
    list_agent_trace_sessions,
    list_agent_trace_turns,
)
from calculus_agent.papers.pdf_export import export_paper_pdf as build_paper_pdf
from calculus_agent.prep.mistakes import create_mistake_prep, get_mistake_prep
from calculus_agent.prep.vision import BailianVisionExtractor as SiliconFlowVisionExtractor
from calculus_agent.papers.latex_renderer import render_paper_latex
from calculus_agent.papers.workflow import (
    BlueprintStateError,
    InfeasiblePaperError,
    WorkflowNotFoundError,
    confirm_blueprint,
    check_blueprint_supply,
    preview_blueprint,
    create_paper as create_saved_paper,
    get_blueprint as get_saved_blueprint,
    get_paper as get_saved_paper,
    load_paper_preview,
    list_paper_history,
    lock_paper_item,
    reorder_paper_items,
    redo_paper_operation,
    replace_paper_item,
    restore_paper_version,
    save_blueprint,
    update_paper_item,
    update_blueprint,
    undo_paper_operations,
    validate_paper as validate_saved_paper,
)
from calculus_agent.questions.review import DraftApprovalError, approve_draft
from calculus_agent.questions.profiling import (
    list_question_profiles,
    profile_approved_questions,
    update_question_profile,
)
from calculus_agent.requirements.parser import (
    OpenAICompatibleRequirementParser,
    apply_blueprint_modification,
)
from calculus_agent.requirements.conversation_agent import PaperConversationAgent
from calculus_agent.orchestration.backend import BailianChatBackend
from calculus_agent.schemas import (
    AgentRunRead,
    AgentRunRequest,
    BlueprintCreateRead,
    CurriculumImportRequest,
    CurriculumNodeRead,
    CMMMathImportRequest,
    DatasetImportRequest,
    DatasetImportSummary,
    DraftApproveRequest,
    DraftProcessRead,
    KnowledgeNodeCreate,
    KnowledgeNodeRead,
    MMMathImportRequest,
    MistakePrepCreate,
    MistakePrepRead,
    NaturalLanguagePaperRequest,
    PaperBlueprint,
    PaperPreviewRead,
    PaperCreateRequest,
    PaperItemUpdate,
    PaperOperationRead,
    PaperLockRequest,
    PaperReorderRequest,
    PaperRestoreRequest,
    PaperUndoRequest,
    QuestionDetailRead,
    QuestionProfileBatchRead,
    QuestionProfileBatchRequest,
    QuestionProfileRead,
    QuestionProfileUpdate,
    QuestionRead,
    VisionQuestionExtractRead,
    VisionQuestionExtractRequest,
    QuestionOptionRead,
    SavedPaperRead,
    SupplyCheckRead,
    ToolCallTraceRead,
    ValidationReportRead,
)
from calculus_agent.ocr.service import (
    create_ocr_task_async,
    create_doc_ocr_task_async,
    get_ocr_task,
    update_ocr_block,
    save_ocr_as_draft,
)
from calculus_agent.solver.service import OpenAICompatibleSolver, ReferenceAnswerSolver


router = APIRouter(prefix="/api/v1")


class TeacherAgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str = Field(min_length=1, max_length=120)
    paper_id: str | None = None
    version_id: str | None = None


class PendingGenerationCardPatchRequest(BaseModel):
    """Structured confirmation-card change; it never contains a full plan."""

    conversation_id: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)
    question_type_patches: list[QuestionTypePatch] = Field(min_length=1)


class PendingGenerationCardPatchResponse(BaseModel):
    status: str
    generation_preview: GenerationPlanPreview


class PendingGenerationConfirmRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)


class PendingGenerationConfirmResponse(BaseModel):
    status: str
    paper: GeneratePaperToolResult


class TeacherAgentSessionMessage(BaseModel):
    role: str
    content: str


class PendingGenerationSessionRead(BaseModel):
    request: GeneratePaperInput
    pending_version: int


class TeacherAgentSessionRead(BaseModel):
    conversation_id: str
    messages: list[TeacherAgentSessionMessage]
    pending_generation: PendingGenerationSessionRead | None = None


def get_session(settings: Settings = Depends(get_settings)) -> Iterator[Session]:
    factory = build_session_factory(settings.database_url)
    with factory.begin() as session:
        yield session


@router.post("/teacher-agent/run", response_model=TeacherAgentResult)
def run_teacher_agent_endpoint(
    request: TeacherAgentRunRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TeacherAgentResult:
    """Thin HTTP adapter for the Phase 2B Teacher Agent workflow."""
    return run_teacher_agent(
        session,
        request.message,
        conversation_id=request.conversation_id,
        paper_id=request.paper_id,
        version_id=request.version_id,
        backend=build_teacher_agent_backend(settings),
    )


class TeacherAgentSpanRead(BaseModel):
    """One observable step inside a Teacher Agent run (read model)."""

    span_id: str
    run_id: str | None = None
    parent_span_id: str | None = None
    span_type: str
    name: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    latency_ms: int | None = None
    input_json: dict | None = None
    output_json: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class TeacherAgentRunRead(BaseModel):
    """Full run-level trace for one Teacher Agent turn (read model)."""

    run_id: str | None = None
    conversation_id: str | None = None
    paper_id: str | None = None
    user_message: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    latency_ms: int | None = None
    agent_name: str
    final_response: str | None = None
    result_status: str
    state_before_json: dict | None = None
    state_after_json: dict | None = None
    error_code: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_stage: str | None = None
    tool_calls_json: list = []
    spans: list[TeacherAgentSpanRead] = []

    model_config = ConfigDict(from_attributes=True)


def _build_teacher_agent_run_read(run: TeacherAgentRunTrace, session: Session) -> TeacherAgentRunRead:
    """Materialize a run row plus its spans into the read model."""
    spans = list(
        session.scalars(
            select(TeacherAgentSpan)
            .where(TeacherAgentSpan.run_id == run.run_id)
            .order_by(TeacherAgentSpan.started_at)
        ).all()
    )
    data = TeacherAgentRunRead.model_validate(run)
    data.spans = [TeacherAgentSpanRead.model_validate(s) for s in spans]
    return data


@router.get("/teacher-agent/runs/{run_id}", response_model=TeacherAgentRunRead)
def get_teacher_agent_run(
    run_id: str,
    session: Session = Depends(get_session),
) -> TeacherAgentRunRead:
    """Recover one turn's full local trace (run + spans) by its run_id."""
    run = (
        session.query(TeacherAgentRunTrace)
        .filter(TeacherAgentRunTrace.run_id == run_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    return _build_teacher_agent_run_read(run, session)


@router.get("/teacher-agent/runs", response_model=list[TeacherAgentRunRead])
def list_teacher_agent_runs(
    conversation_id: str = Query(min_length=1, max_length=120),
    session: Session = Depends(get_session),
) -> list[TeacherAgentRunRead]:
    """List every turn's local trace for a conversation, oldest first."""
    runs = (
        session.query(TeacherAgentRunTrace)
        .filter(TeacherAgentRunTrace.conversation_id == conversation_id)
        .order_by(TeacherAgentRunTrace.started_at)
        .all()
    )
    return [_build_teacher_agent_run_read(run, session) for run in runs]


@router.get("/teacher-agent/session", response_model=TeacherAgentSessionRead)
def get_teacher_agent_session(
    conversation_id: str = Query(min_length=1, max_length=120),
    session: Session = Depends(get_session),
) -> TeacherAgentSessionRead:
    """Recover the database-backed UI state; traces are deliberately excluded."""
    history = DatabaseConversationHistoryStore(session).list_messages(conversation_id)
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    return TeacherAgentSessionRead(
        conversation_id=conversation_id,
        messages=[TeacherAgentSessionMessage.model_validate(item) for item in history],
        pending_generation=(
            PendingGenerationSessionRead(
                request=pending.request,
                pending_version=pending.pending_version,
            )
            if pending is not None else None
        ),
    )


def _pending_generation_service(
    session: Session,
    conversation_id: str,
    *,
    expected_version: int | None = None,
) -> GenerationService:
    return GenerationService(
        session=session,
        store=DatabasePendingReplacementStore(session),
        conversation_id=conversation_id,
        expected_pending_generation_version=expected_version,
        runtime_state_service=RuntimeStateService(session),
    )


def _assert_current_pending_version(
    service: GenerationService,
    expected_version: int,
) -> None:
    try:
        service.assert_pending_version(expected_version)
    except NoPendingGenerationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "no_pending_generation"},
        ) from exc
    except PendingGenerationStaleError as exc:
        pending = service.store.get_generation(service.conversation_id)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_pending_plan",
                "current_version": (
                    pending.pending_version if pending is not None else None
                ),
            },
        ) from exc


@router.post(
    "/teacher-agent/pending-generation/update",
    response_model=PendingGenerationCardPatchResponse,
)
def update_pending_generation_from_card(
    request: PendingGenerationCardPatchRequest,
    session: Session = Depends(get_session),
) -> PendingGenerationCardPatchResponse:
    """Apply a card patch through the same deterministic preview pipeline as chat."""
    service = _pending_generation_service(
        session,
        request.conversation_id,
        expected_version=request.expected_version,
    )
    _assert_current_pending_version(service, request.expected_version)
    try:
        preview = service.preview(
            GenerationPlanPatch(
                question_type_patches=request.question_type_patches
            )
        )
    except PendingGenerationStaleError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "stale_pending_plan"},
        ) from exc
    return PendingGenerationCardPatchResponse(
        status=(
            "waiting_confirmation"
            if preview.ok
            else "needs_clarification"
        ),
        generation_preview=preview,
    )


@router.post(
    "/teacher-agent/pending-generation/confirm",
    response_model=PendingGenerationConfirmResponse,
)
def confirm_pending_generation_from_card(
    request: PendingGenerationConfirmRequest,
    session: Session = Depends(get_session),
) -> PendingGenerationConfirmResponse:
    """Execute the persisted pending plan without an LLM or client-supplied plan."""
    service = _pending_generation_service(
        session,
        request.conversation_id,
        expected_version=request.expected_version,
    )
    _assert_current_pending_version(service, request.expected_version)
    try:
        paper = service.confirm()
    except NoPendingGenerationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "no_pending_generation"},
        ) from exc

    status = (
        "completed"
        if paper.ok
        else "needs_clarification"
        if paper.needs_clarification
        else "failed"
    )
    return PendingGenerationConfirmResponse(status=status, paper=paper)


@router.get("/admin/agent-traces/sessions")
def get_agent_trace_sessions(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[dict]]:
    """Admin-only boundary for the file-backed Teacher Agent trace index.

    Authentication is intentionally not introduced in this first version because the
    project has no existing administrator identity model to reuse.
    """
    return {"items": list_agent_trace_sessions()[:limit]}


@router.get("/admin/agent-traces/turns")
def get_agent_trace_turns(
    conversation_id: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, list[dict]]:
    return {"items": list_agent_trace_turns(conversation_id)[:limit]}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "application": "math-paper-agent"}


@router.post("/prep/mistakes", response_model=MistakePrepRead)
def create_mistake_prep_task(
    request: MistakePrepCreate, session: Session = Depends(get_session)
) -> MistakePrepRead:
    return create_mistake_prep(session, request)


@router.get("/prep/mistakes/{task_id}", response_model=MistakePrepRead)
def mistake_prep_task(
    task_id: str, session: Session = Depends(get_session)
) -> MistakePrepRead:
    result = get_mistake_prep(session, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="错题备课任务不存在")
    return result


@router.post("/prep/vision/extract", response_model=VisionQuestionExtractRead)
def extract_question_images(
    request: VisionQuestionExtractRequest,
    settings: Settings = Depends(get_settings),
) -> VisionQuestionExtractRead:
    if not settings.siliconflow_api_key:
        raise HTTPException(
            status_code=503,
            detail="尚未配置 SiliconFlow API Key，请设置 SILICONFLOW_API_KEY",
        )
    extractor = SiliconFlowVisionExtractor(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.siliconflow_vl_model,
        timeout=settings.siliconflow_timeout_seconds,
    )
    try:
        return extractor.extract(request.question_image, request.solution_image)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"SiliconFlow 视觉识别失败：{error}",
        ) from error


@router.post("/agents/runs", response_model=AgentRunRead)
def create_agent_run(
    request: AgentRunRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AgentRunRead:
    if not settings.siliconflow_api_key:
        raise HTTPException(
            status_code=503,
            detail="尚未配置 SiliconFlow API Key，请设置 SILICONFLOW_API_KEY",
        )
    return run_paper_agent(
        session,
        request,
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.siliconflow_agent_model,
        timeout=settings.siliconflow_timeout_seconds,
    )


@router.get("/agents/runs/{run_id}", response_model=AgentRunRead)
def get_agent_run(run_id: str, session: Session = Depends(get_session)) -> AgentRunRead:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    traces = list(
        session.scalars(
            select(ToolCallTrace)
            .where(ToolCallTrace.run_id == run.id)
            .order_by(ToolCallTrace.step, ToolCallTrace.created_at)
        ).all()
    )
    return AgentRunRead(
        run_id=run.id,
        status=run.status,
        mode=run.mode,
        final_response=run.final_response,
        steps_used=run.steps_used,
        error_message=run.error_message,
        traces=[
            ToolCallTraceRead(
                step=item.step,
                actor=item.actor,
                tool_name=item.tool_name,
                arguments=item.arguments_json,
                result=item.result_json,
                status=item.status,
                duration_ms=item.duration_ms,
            )
            for item in traces
        ],
    )


@router.post("/curriculum/import", response_model=list[CurriculumNodeRead])
def add_curriculum(
    request: CurriculumImportRequest, session: Session = Depends(get_session)
) -> list[CurriculumNodeRead]:
    nodes = import_curriculum(session, request.directory_text)
    return [
        CurriculumNodeRead(
            id=node.id,
            parent_id=node.parent_id,
            node_type=node.node_type,
            code=node.code,
            title=node.title,
            sort_order=node.sort_order,
        )
        for node in nodes
    ]


@router.get("/curriculum", response_model=list[CurriculumNodeRead])
def curriculum(session: Session = Depends(get_session)) -> list[CurriculumNodeRead]:
    nodes = session.scalars(select(CurriculumNode).order_by(CurriculumNode.sort_order)).all()
    return [CurriculumNodeRead.model_validate(node, from_attributes=True) for node in nodes]


@router.post("/knowledge", response_model=KnowledgeNodeRead)
def add_knowledge_node(
    request: KnowledgeNodeCreate, session: Session = Depends(get_session)
) -> KnowledgeNodeRead:
    normalized = normalize_name(request.name)
    existing = session.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.node_type == request.node_type,
            KnowledgeNode.normalized_name == normalized,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Knowledge node already exists")
    node = KnowledgeNode(
        curriculum_node_id=request.curriculum_node_id,
        node_type=request.node_type,
        name=request.name.strip(),
        normalized_name=normalized,
        description=request.description,
        source_type="teacher" if request.review_status == "approved" else "agent_candidate",
        confidence=1.0 if request.review_status == "approved" else 0.7,
        review_status=request.review_status,
    )
    session.add(node)
    session.flush()
    for alias in request.aliases:
        session.add(
            KnowledgeAlias(
                node_id=node.id,
                alias=alias.strip(),
                normalized_alias=normalize_name(alias),
            )
        )
    return KnowledgeNodeRead(
        id=node.id,
        node_type=node.node_type,
        name=node.name,
        curriculum_node_id=node.curriculum_node_id,
        review_status=node.review_status,
    )


@router.get("/knowledge/search", response_model=list[KnowledgeNodeRead])
def search_knowledge(
    query: str = Query(min_length=1),
    node_type: str | None = None,
    session: Session = Depends(get_session),
) -> list[KnowledgeNodeRead]:
    return [
        KnowledgeNodeRead(
            id=item.node.id,
            node_type=item.node.node_type,
            name=item.node.name,
            curriculum_node_id=item.node.curriculum_node_id,
            review_status=item.node.review_status,
            score=item.score,
            match_reasons=item.reasons,
        )
        for item in retrieve_knowledge(session, query, node_type=node_type)
    ]


class KnowledgeConfirmRequest(BaseModel):
    knowledge_node_ids: list[str] = Field(min_length=1, max_length=3)
    ai_prediction: list[dict] = Field(default_factory=list)


@router.post("/questions/{question_id}/knowledge/classify")
def classify_question_knowledge_route(question_id: str, session: Session = Depends(get_session)) -> dict:
    question = session.get(Question, question_id)
    if question is None or not question.is_active:
        raise HTTPException(status_code=404, detail="正式题目不存在")
    draft = session.get(QuestionDraft, question.draft_id)
    if draft is not None and draft.source_name == "ocr_import":
        ocr_draft = session.get(OcrImportDraft, draft.source_item_id)
        if ocr_draft is not None and not ocr_draft.content_confirmed:
            raise HTTPException(status_code=409, detail="请先确认题目内容")
    return classify_knowledge_points(session, question)


@router.post("/knowledge/classification/suggestions")
def knowledge_classification_suggestions(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """为尚未标注的 OCR 正式题生成受控知识点候选。"""
    linked = select(QuestionKnowledgeLink.question_id)
    questions = list(session.scalars(
        select(Question)
        .join(QuestionDraft, Question.draft_id == QuestionDraft.id)
        .where(
            Question.review_status == "approved",
            Question.is_active.is_(True),
            QuestionDraft.source_name.in_(("ocr_import", "ocr_doc")),
            Question.id.not_in(linked),
        )
        .order_by(Question.id)
        .limit(limit)
    ).all())
    return {
        "items": [
            {
                "question_id": question.id,
                "question_text": question.question_text,
                "question_type": canonical_question_type(question.question_type),
                "suggestions": suggest_question_knowledge(session, question),
            }
            for question in questions
        ],
        "remaining": len(questions),
    }


@router.post("/questions/{question_id}/knowledge/confirm")
def confirm_question_knowledge_route(
    question_id: str,
    request: KnowledgeConfirmRequest,
    session: Session = Depends(get_session),
) -> dict:
    question = session.get(Question, question_id)
    if question is None or question.review_status != "approved" or not question.is_active:
        raise HTTPException(status_code=404, detail="正式题目不存在")
    try:
        confirm_question_knowledge(session, question_id, request.knowledge_node_ids, ai_prediction=request.ai_prediction)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "question_id": question_id,
        "knowledge": _load_knowledge_names(session, [question_id]).get(question_id, []),
    }


def _load_knowledge_names(session: Session, question_ids: list[str]) -> dict[str, list[str]]:
    """批量取知识点名称，一次 SQL 覆盖全部题目，避免 N+1。"""
    if not question_ids:
        return {}
    rows = session.execute(
        select(QuestionKnowledgeLink.question_id, KnowledgeNode.name)
        .join(KnowledgeNode, KnowledgeNode.id == QuestionKnowledgeLink.knowledge_node_id)
        .where(QuestionKnowledgeLink.question_id.in_(question_ids))
    ).all()
    mapping: dict[str, list[str]] = {}
    for question_id, name in rows:
        mapping.setdefault(question_id, []).append(name)
    return mapping


def _load_knowledge_node_ids(session: Session, question_id: str) -> list[str]:
    return list(session.scalars(
        select(QuestionKnowledgeLink.knowledge_node_id)
        .where(QuestionKnowledgeLink.question_id == question_id)
        .order_by(
            QuestionKnowledgeLink.relation_type,
            QuestionKnowledgeLink.id,
        )
    ).all())


def _load_question_origins(
    session: Session, question_ids: list[str]
) -> dict[str, tuple[str | None, str | None, int | None]]:
    """批量回查 OCR 导入题的来源信息，一次 join 完成，避免 N+1。

    链路：Question.draft_id → QuestionDraft(source_name='ocr_import')
          → OcrImportDraft(source_item_id) → OcrImportSource
    非 OCR 来源（built-in-demo / 数据集导入等）不会出现在结果中，调用方按 None 处理。
    """
    if not question_ids:
        return {}
    rows = session.execute(
        select(
            Question.id,
            OcrImportDraft.original_number,
            OcrImportSource.original_name,
            OcrImportDraft.page_number,
        )
        .join(QuestionDraft, QuestionDraft.id == Question.draft_id)
        .join(OcrImportDraft, OcrImportDraft.id == QuestionDraft.source_item_id)
        .join(OcrImportSource, OcrImportSource.id == OcrImportDraft.source_id)
        .where(
            Question.id.in_(question_ids),
            QuestionDraft.source_name == "ocr_import",
        )
    ).all()
    return {
        question_id: (original_number, original_name, page_number)
        for question_id, original_number, original_name, page_number in rows
    }


@router.get("/questions/search", response_model=list[QuestionOptionRead])
def search_questions(
    query: str = Query(default="", max_length=200),
    question_type: str | None = None,
    source_name: str | None = None,
    publish_source: str | None = None,
    chapter_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    difficulty_min: int | None = Query(default=None, ge=1, le=5),
    difficulty_max: int | None = Query(default=None, ge=1, le=5),
    calculation_load_max: int | None = Query(default=None, ge=1, le=5),
    comprehensive_level_min: int | None = Query(default=None, ge=1, le=5),
    estimated_time_max: int | None = Query(default=None, ge=1, le=180),
    session: Session = Depends(get_session),
) -> list[QuestionOptionRead]:
    """正式题库列表。

    - 默认只返回 review_status='approved'。
    - source_name 可选：传入时按 QuestionDraft.source_name 过滤（如 'ocr_import' 只展示用户自己导入并发布的题）。
      built-in-demo / test_source / 数据集等示例数据不传该参数时仍可被检索，但「我的题库」默认传 'ocr_import' 以隐藏它们。
    """
    # Keep direct Python callers/tests compatible with FastAPI's Query defaults.
    difficulty_min = difficulty_min if isinstance(difficulty_min, int) else None
    difficulty_max = difficulty_max if isinstance(difficulty_max, int) else None
    calculation_load_max = calculation_load_max if isinstance(calculation_load_max, int) else None
    comprehensive_level_min = comprehensive_level_min if isinstance(comprehensive_level_min, int) else None
    estimated_time_max = estimated_time_max if isinstance(estimated_time_max, int) else None
    statement = select(Question).where(
        Question.review_status == "approved",
        Question.is_active.is_(True),
    )
    profile_filters = any(value is not None for value in (
        difficulty_min, difficulty_max, calculation_load_max,
        comprehensive_level_min, estimated_time_max,
    ))
    if profile_filters:
        latest_approved = (
            select(
                QuestionProfile.question_id,
                func.max(QuestionProfile.profile_version).label("profile_version"),
            )
            .where(QuestionProfile.profile_status == "approved")
            .group_by(QuestionProfile.question_id)
            .subquery()
        )
        statement = statement.join(
            latest_approved, latest_approved.c.question_id == Question.id
        ).join(
            QuestionProfile,
            (QuestionProfile.question_id == latest_approved.c.question_id)
            & (QuestionProfile.profile_version == latest_approved.c.profile_version),
        )
        if difficulty_min is not None:
            statement = statement.where(QuestionProfile.difficulty >= difficulty_min)
        if difficulty_max is not None:
            statement = statement.where(QuestionProfile.difficulty <= difficulty_max)
        if calculation_load_max is not None:
            statement = statement.where(QuestionProfile.calculation_load <= calculation_load_max)
        if comprehensive_level_min is not None:
            statement = statement.where(QuestionProfile.comprehensive_level >= comprehensive_level_min)
        if estimated_time_max is not None:
            statement = statement.where(QuestionProfile.estimated_time_min <= estimated_time_max)
    if source_name:
        source_names = [item.strip() for item in source_name.split(",") if item.strip()]
        statement = statement.join(
            QuestionDraft, QuestionDraft.id == Question.draft_id
        ).where(QuestionDraft.source_name.in_(source_names))
    if publish_source in {"manual", "ai_auto"}:
        statement = statement.where(Question.publish_source == publish_source)
    search_text = query.strip()
    if search_text:
        text_match = Question.question_text.contains(search_text)
        normalized_id_query = search_text.lower()
        if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            normalized_id_query,
        ):
            statement = statement.where(
                or_(Question.id == normalized_id_query, text_match)
            )
        elif re.fullmatch(r"[0-9a-f]{8,12}", normalized_id_query):
            statement = statement.where(
                or_(
                    func.replace(Question.id, "-", "").startswith(normalized_id_query),
                    text_match,
                )
            )
        elif re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{0,3}", normalized_id_query):
            statement = statement.where(
                or_(Question.id.startswith(normalized_id_query), text_match)
            )
        else:
            statement = statement.where(text_match)
    if question_type:
        # Canonicalize the filter param so a raw alias (e.g. "proof") still
        # resolves to its canonical type ("证明题").
        statement = statement.where(
            Question.question_type == canonical_question_type(question_type)
        )
    if chapter_id:
        # chapter_id is already a stable DB identifier. If it resolves to an
        # approved chapter, filter directly by materialized ownership.
        chapter = session.get(CurriculumNode, chapter_id)
        if (
            chapter is not None
            and chapter.node_type == "chapter"
            and chapter.review_status == "approved"
        ):
            statement = statement.where(
                Question.curriculum_chapter_id == chapter.id
            )
        # Preserve existing invalid-id behavior: unknown id means no filter.
    questions = session.scalars(statement.order_by(Question.created_at.desc()).limit(limit)).all()
    question_ids = [question.id for question in questions]
    knowledge_map = _load_knowledge_names(session, question_ids)
    origin_map = _load_question_origins(session, question_ids)
    unowned_ids = [
        question.id
        for question in questions
        if question.curriculum_chapter_id is None
    ]
    unowned_status = resolve_questions_chapters(session, unowned_ids)
    owner_ids = {
        question.curriculum_chapter_id
        for question in questions
        if question.curriculum_chapter_id
    }
    owner_chapters = {
        chapter.id: chapter
        for chapter in (
            session.scalars(
                select(CurriculumNode).where(CurriculumNode.id.in_(owner_ids))
            ).all()
            if owner_ids else []
        )
    }
    latest_profiles = {
        item.question_id: item
        for item in list_question_profiles(session, status="approved", source_name=None)
        if item.question_id in question_ids
    }
    results = []
    for question in questions:
        original_number, source_name, source_page = origin_map.get(
            question.id, (None, None, None)
        )
        owner_chapter = owner_chapters.get(question.curriculum_chapter_id)
        if owner_chapter is not None:
            chapter_name = chapter_display_name(owner_chapter)
            resolved_chapter_id = owner_chapter.id
            chapter_status = "ok"
        else:
            unresolved = unowned_status.get(question.id)
            chapter_name = None
            resolved_chapter_id = None
            chapter_status = (
                unresolved.status if unresolved is not None else "unresolvable"
            )
        results.append(
            QuestionOptionRead(
                id=question.id,
                question_text=question.question_text,
                question_type=canonical_question_type(question.question_type),
                knowledge=knowledge_map.get(question.id, []),
                original_number=original_number,
                source_name=source_name,
                source_page=source_page,
                chapter=chapter_name,
                chapter_id=resolved_chapter_id,
                chapter_status=chapter_status,
                knowledge_match_status=question.knowledge_match_status,
                difficulty=(profile.difficulty if (profile := latest_profiles.get(question.id)) else None),
                estimated_time_min=profile.estimated_time_min if profile else None,
                reasoning_depth=profile.reasoning_depth if profile else None,
                calculation_load=profile.calculation_load if profile else None,
                comprehensive_level=profile.comprehensive_level if profile else None,
                publish_source=question.publish_source,
                quality_sample_required=question.quality_sample_required,
            )
        )
    return results


@router.get("/questions/edit-options")
def question_edit_options(session: Session = Depends(get_session)) -> dict:
    rows = session.execute(
        select(KnowledgeNode.id, KnowledgeNode.name, CurriculumNode.title)
        .join(CurriculumNode, CurriculumNode.id == KnowledgeNode.curriculum_node_id)
        .join(Textbook, Textbook.id == CurriculumNode.textbook_id)
        .where(
            Textbook.is_active.is_(True),
            KnowledgeNode.review_status == "approved",
            CurriculumNode.node_type.in_(("section", "knowledge_point")),
        )
        .order_by(CurriculumNode.sort_order, KnowledgeNode.name)
    ).all()
    return {
        "knowledge": [
            {"id": node_id, "name": name, "label": title}
            for node_id, name, title in rows
        ]
    }


class FormalQuestionUpdateRequest(BaseModel):
    question_text: str = Field(min_length=1, max_length=100000)
    solution_content: str | None = Field(default=None, max_length=200000)
    # 保留旧字段以兼容历史客户端；正式题编辑 UI 不再提交或维护它。
    final_answer: str | None = Field(default=None, max_length=50000)
    question_type: str = Field(min_length=1, max_length=40)
    chapter: str | None = Field(default=None, max_length=255)
    knowledge_node_ids: list[str] = Field(default_factory=list, max_length=3)
    difficulty: int = Field(ge=1, le=5)
    original_number: str | None = Field(default=None, max_length=40)
    source_page: int | None = Field(default=None, ge=1)


class QuestionTypePatchRequest(BaseModel):
    question_type: str = Field(min_length=1, max_length=40)


@router.patch("/questions/{question_id}", response_model=QuestionDetailRead)
def update_formal_question(
    question_id: str,
    request: FormalQuestionUpdateRequest,
    session: Session = Depends(get_session),
) -> QuestionDetailRead:
    question = session.get(Question, question_id)
    if question is None or not question.is_active:
        raise HTTPException(status_code=404, detail="正式题目不存在")
    draft = session.get(QuestionDraft, question.draft_id)
    if draft is None:
        raise HTTPException(status_code=409, detail="正式题缺少来源草稿")

    selected_ids = list(dict.fromkeys(request.knowledge_node_ids))
    if len(selected_ids) != len(request.knowledge_node_ids):
        raise HTTPException(status_code=422, detail="知识点不能重复")
    approved_ids = set(session.scalars(
        select(KnowledgeNode.id)
        .join(CurriculumNode, CurriculumNode.id == KnowledgeNode.curriculum_node_id)
        .join(Textbook, Textbook.id == CurriculumNode.textbook_id)
        .where(
            KnowledgeNode.id.in_(selected_ids),
            KnowledgeNode.review_status == "approved",
            Textbook.is_active.is_(True),
            CurriculumNode.node_type.in_(("section", "knowledge_point")),
        )
    ).all()) if selected_ids else set()
    if approved_ids != set(selected_ids):
        raise HTTPException(status_code=422, detail="只能选择当前激活教材的受控知识点")

    current_ids = _load_knowledge_node_ids(session, question.id)
    content_changed = any((
        question.question_text != request.question_text.strip(),
        (draft.solution_text or "") != (request.solution_content or ""),
        request.final_answer is not None and (question.final_answer or "") != request.final_answer,
        question.question_type != request.question_type,
    ))
    knowledge_changed = current_ids != selected_ids

    question.question_text = request.question_text.strip()
    question.question_type = request.question_type
    if request.final_answer is not None:
        question.final_answer = request.final_answer or None
    question.solution_json = {
        "solution_steps": [request.solution_content] if request.solution_content else []
    }
    question.review_status = "approved"
    draft.question_text = question.question_text
    draft.question_type = request.question_type
    draft.solution_text = request.solution_content or None
    draft.status = "approved"

    # 正式题人工修改时直接创建一个 approved 人工画像版本；不改变旧字段兼容性。
    current_profile = session.scalar(
        select(QuestionProfile)
        .where(QuestionProfile.question_id == question.id)
        .order_by(QuestionProfile.profile_version.desc())
    )
    if current_profile is not None:
        session.add(QuestionProfile(
            question_id=question.id,
            profile_version=current_profile.profile_version + 1,
            difficulty=request.difficulty,
            estimated_time_min=current_profile.estimated_time_min,
            reasoning_depth=current_profile.reasoning_depth,
            calculation_load=current_profile.calculation_load,
            knowledge_depth=current_profile.knowledge_depth,
            comprehensive_level=current_profile.comprehensive_level,
            confidence=current_profile.confidence,
            profile_source="human",
            profile_status="approved",
            reason="题库正式题编辑时由教师确认难度",
            reviewed_at=datetime.now(UTC),
        ))
    else:
        session.add(QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=request.difficulty,
            estimated_time_min={1: 2, 2: 4, 3: 7, 4: 11, 5: 15}[request.difficulty],
            reasoning_depth=2,
            calculation_load=2,
            knowledge_depth=1,
            comprehensive_level=1,
            confidence=1.0,
            profile_source="human",
            profile_status="approved",
            reason="题库正式题编辑时由教师确认难度",
            reviewed_at=datetime.now(UTC),
        ))

    if knowledge_changed:
        session.execute(delete(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.question_id == question.id
        ))
        for node_id in selected_ids:
            session.add(QuestionKnowledgeLink(
                question_id=question.id,
                knowledge_node_id=node_id,
                relation_type="related",
                confidence=1.0,
                evidence_json=[{
                    "source": "manual_question_edit",
                    "basis": "题库人工修改时确认",
                }],
            ))
        question.knowledge_match_status = "current" if selected_ids else "unmatched"
        session.flush()
        sync_question_chapter_ownership(session, question.id)
    elif content_changed:
        question.knowledge_match_status = "stale"

    if draft.source_name == "ocr_import":
        origin = session.get(OcrImportDraft, draft.source_item_id)
        if origin is not None:
            if request.original_number:
                origin.original_number = request.original_number.strip()
            if request.source_page is not None:
                origin.page_number = request.source_page
    session.flush()
    return get_question_detail(question.id, session)


def patch_question_type_value(
    session: Session, question_id: str, raw_type: str
) -> Question:
    """仅修改题型：确定性 CRUD，不调用 LLM。

    - 严格校验题型合法性（canonical 后必须在允许集合内），非法抛 ValueError。
    - 题型归一化为 canonical 中文值后存储（question + 来源 draft 同步）。
    - 保持既有审核状态（review_status / verification_status）不变。
    - 不触碰知识点关联，不会触发「只能选择当前激活教材知识点」校验。
    - 记录修改时间到 updated_at。
    """
    question = session.get(Question, question_id)
    if question is None or not question.is_active:
        raise ValueError("正式题目不存在")
    canonical = canonical_question_type(raw_type)
    if canonical not in ALLOWED_QUESTION_TYPES:
        raise ValueError("非法的题型")
    draft = session.get(QuestionDraft, question.draft_id)
    question.question_type = canonical
    if draft is not None:
        draft.question_type = canonical
    question.updated_at = datetime.now(UTC)
    session.flush()
    return question


@router.patch("/questions/{question_id}/question-type", response_model=QuestionDetailRead)
def patch_question_type(
    question_id: str,
    request: QuestionTypePatchRequest,
    session: Session = Depends(get_session),
) -> QuestionDetailRead:
    try:
        question = patch_question_type_value(session, question_id, request.question_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return get_question_detail(question.id, session)


@router.delete("/questions/{question_id}")
def retire_formal_question(
    question_id: str, session: Session = Depends(get_session)
) -> dict:
    question = session.get(Question, question_id)
    if question is None or not question.is_active:
        raise HTTPException(status_code=404, detail="正式题目不存在")
    question.is_active = False
    question.knowledge_match_status = "retired"
    session.flush()
    return {"question_id": question.id, "is_active": False, "deleted": False}


@router.post("/question-profiles/batch", response_model=QuestionProfileBatchRead)
def create_question_profile_batch(
    request: QuestionProfileBatchRequest,
    session: Session = Depends(get_session),
) -> QuestionProfileBatchRead:
    return profile_approved_questions(
        session, source_name=request.source_name, force=request.force
    )


@router.get("/question-profiles", response_model=list[QuestionProfileRead])
def read_question_profiles(
    status: str | None = Query(default=None),
    source_name: str | None = Query(default="ocr_import"),
    session: Session = Depends(get_session),
) -> list[QuestionProfileRead]:
    if status not in {None, "pending", "approved", "needs_review"}:
        raise HTTPException(status_code=422, detail="Unknown profile status")
    return list_question_profiles(session, status=status, source_name=source_name)


@router.patch("/question-profiles/{profile_id}", response_model=QuestionProfileRead)
def patch_question_profile(
    profile_id: str,
    request: QuestionProfileUpdate,
    session: Session = Depends(get_session),
) -> QuestionProfileRead:
    try:
        return update_question_profile(session, profile_id, request)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/questions/{question_id}", response_model=QuestionDetailRead)
def get_question_detail(
    question_id: str, session: Session = Depends(get_session)
) -> QuestionDetailRead:
    """正式题详情（题库人工查看层）。

    参考解答直接使用 QuestionDraft.solution_text 完整文本块，
    不从 Question.solution_json.solution_steps 重组。
    """
    row = session.execute(
        select(Question, QuestionDraft.solution_text)
        .join(QuestionDraft, QuestionDraft.id == Question.draft_id)
        .where(
            Question.id == question_id,
            Question.review_status == "approved",
            Question.is_active.is_(True),
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    question, solution_text = row
    original_number, source_name, source_page = _load_question_origins(
        session, [question.id]
    ).get(question.id, (None, None, None))
    chapter_resolution = resolve_question_chapter(session, question.id)
    # Authoritative chapter projection: never derive chapter_id from
    # knowledge points. Knowledge may span chapters.
    owner_chapter_id = question.curriculum_chapter_id
    owner_chapter = (
        session.get(CurriculumNode, owner_chapter_id)
        if owner_chapter_id
        else None
    )
    return QuestionDetailRead(
        id=question.id,
        question_text=question.question_text,
        question_type=canonical_question_type(question.question_type),
        knowledge=_load_knowledge_names(session, [question.id]).get(question.id, []),
        knowledge_node_ids=_load_knowledge_node_ids(session, question.id),
        solution_content=solution_text or None,
        final_answer=question.final_answer,
        chapter=chapter_display_name(owner_chapter),
        chapter_id=owner_chapter_id,
        chapter_status=chapter_resolution.status,
        original_number=original_number,
        source_name=source_name,
        source_page=source_page,
        difficulty=session.scalar(
            select(QuestionProfile.difficulty)
            .where(
                QuestionProfile.question_id == question.id,
                QuestionProfile.profile_status == "approved",
            )
            .order_by(QuestionProfile.profile_version.desc())
            .limit(1)
        ),
        knowledge_match_status=question.knowledge_match_status,
        is_active=question.is_active,
        publish_source=question.publish_source,
        quality_sample_required=question.quality_sample_required,
        ai_review=question.ai_review_json,
    )


@router.post("/datasets/ugmathbench/import", response_model=DatasetImportSummary)
def import_dataset(
    request: DatasetImportRequest, session: Session = Depends(get_session)
) -> DatasetImportSummary:
    path = Path(request.path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Dataset file not found")
    return import_ugmathbench(session, path, variants=request.variants, limit=request.limit)


@router.post("/datasets/mm-math/import", response_model=DatasetImportSummary)
def import_chinese_dataset(
    request: MMMathImportRequest, session: Session = Depends(get_session)
) -> DatasetImportSummary:
    path = Path(request.path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Dataset file not found")
    image_root = Path(request.image_root).expanduser().resolve() if request.image_root else None
    return import_mm_math(
        session,
        path,
        image_root=image_root,
        limit=request.limit,
        publish=request.publish,
    )


@router.post("/datasets/cmm-math/import", response_model=DatasetImportSummary)
def import_chinese_k12_dataset(
    request: CMMMathImportRequest, session: Session = Depends(get_session)
) -> DatasetImportSummary:
    path = Path(request.path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Dataset file not found")
    image_root = Path(request.image_root).expanduser().resolve() if request.image_root else None
    return import_cmm_math(
        session,
        path,
        levels=tuple(request.levels),
        image_root=image_root,
        text_only=request.text_only,
        require_analysis=request.require_analysis,
        limit=request.limit,
        publish=request.publish,
    )


@router.post("/blueprints/parse", response_model=BlueprintCreateRead)
def parse_blueprint(
    request: NaturalLanguagePaperRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BlueprintCreateRead:
    if request.base_blueprint_id:
        try:
            base_record = get_saved_blueprint(session, request.base_blueprint_id)
            base = base_record.blueprint
        except WorkflowNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        paper = None
        if request.current_paper_id:
            try:
                paper = load_paper_preview(session, request.current_paper_id)
            except WorkflowNotFoundError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
        if paper is not None:
            undo_match = re.search(r"撤销(?:最后|刚才)?\s*(\d+)?\s*(?:步|次|个操作)?|上一道.*(?:换回来|恢复)", request.requirement)
            redo_match = re.search(r"重做|恢复刚才撤销", request.requirement)
            restore_match = re.search(r"(?:恢复|回到).*?(?:版本|第)\s*(\d+)\s*(?:版)?", request.requirement)
            try:
                restored = None
                if redo_match:
                    restored = redo_paper_operation(session, request.current_paper_id)
                elif restore_match:
                    current_record = session.get(Paper, request.current_paper_id)
                    root_id = current_record.root_paper_id or current_record.id
                    target = session.scalar(
                        select(Paper).where(
                            Paper.root_paper_id == root_id,
                            Paper.version == int(restore_match.group(1)),
                        )
                    )
                    if target is None and int(restore_match.group(1)) == 1:
                        target = session.get(Paper, root_id)
                    if target is None:
                        raise WorkflowNotFoundError("指定的试卷版本不存在")
                    restored = restore_paper_version(
                        session, request.current_paper_id, target.id
                    )
                elif undo_match:
                    count = int(undo_match.group(1) or 1)
                    restored = undo_paper_operations(
                        session, request.current_paper_id, count=count
                    )
                if restored is not None:
                    return base_record.model_copy(
                        update={
                            "agent_message": f"已恢复为试卷版本 {restored.version}。",
                            "paper_result": restored.model_dump(mode="json"),
                        }
                    )
            except (WorkflowNotFoundError, BlueprintStateError) as error:
                return base_record.model_copy(
                    update={"agent_message": str(error), "needs_clarification": True}
                )
        try:
            return save_blueprint(
                session, apply_blueprint_modification(request.requirement, base)
            ).model_copy(update={"agent_message": "已更新当前组卷方案。"})
        except ValueError:
            if not settings.siliconflow_api_key:
                return base_record.model_copy(
                    update={
                        "agent_message": "我还不能唯一确定这次修改。请说明要调整的题型、题数或具体题号。",
                        "needs_clarification": True,
                    }
                )
            backend = BailianChatBackend(
                api_key=settings.siliconflow_api_key,
                base_url=settings.siliconflow_base_url,
                model=settings.siliconflow_agent_model,
                timeout=settings.siliconflow_timeout_seconds,
            )
            try:
                action, message, updated = PaperConversationAgent(backend).decide(
                    request.requirement,
                    base,
                    paper=paper,
                    history=request.conversation_history,
                )
                if action == "clarify" or updated is None:
                    return base_record.model_copy(
                        update={"agent_message": message, "needs_clarification": True}
                    )
                return save_blueprint(session, updated).model_copy(
                    update={"agent_message": message}
                )
            except Exception as error:
                return base_record.model_copy(
                    update={
                        "agent_message": f"我没有可靠理解这次修改，请换一种方式说明。具体原因：{error}",
                        "needs_clarification": True,
                    }
                )
    normalized = " ".join(request.requirement.split())
    requirement_hash = hashlib.sha256(f"blueprint-v1:{normalized}".encode()).hexdigest()
    cached = session.get(RequirementParseCache, requirement_hash)
    if cached is not None:
        result = save_blueprint(session, PaperBlueprint.model_validate(cached.blueprint_json))
        return result.model_copy(update={"cached": True})
    parser = _requirement_parser(settings)
    try:
        blueprint = parser.parse(request.requirement)
        session.add(
            RequirementParseCache(
                requirement_hash=requirement_hash,
                requirement_text=normalized,
                blueprint_json=blueprint.model_dump(mode="json"),
            )
        )
        return save_blueprint(session, blueprint)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"需求解析失败：{error}") from error


@router.get("/blueprints/{blueprint_id}/supply-check", response_model=SupplyCheckRead)
def blueprint_supply_check(
    blueprint_id: str, session: Session = Depends(get_session)
) -> SupplyCheckRead:
    try:
        return check_blueprint_supply(session, blueprint_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/blueprints/{blueprint_id}/candidate-preview", response_model=PaperPreviewRead)
def blueprint_candidate_preview(
    blueprint_id: str, session: Session = Depends(get_session)
) -> PaperPreviewRead:
    """组卷前预览候选题；不确认蓝图、不创建试卷。"""
    try:
        return preview_blueprint(session, blueprint_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _requirement_parser(settings: Settings):
    if not settings.siliconflow_api_key:
        raise HTTPException(
            status_code=503,
            detail="尚未配置 SiliconFlow API Key，请设置 SILICONFLOW_API_KEY",
        )
    return OpenAICompatibleRequirementParser(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.siliconflow_agent_model,
        timeout=settings.siliconflow_timeout_seconds,
    )


@router.get("/blueprints/{blueprint_id}", response_model=BlueprintCreateRead)
def read_blueprint(
    blueprint_id: str, session: Session = Depends(get_session)
) -> BlueprintCreateRead:
    try:
        return get_saved_blueprint(session, blueprint_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/blueprints/{blueprint_id}", response_model=BlueprintCreateRead)
def patch_blueprint(
    blueprint_id: str,
    blueprint: PaperBlueprint,
    session: Session = Depends(get_session),
) -> BlueprintCreateRead:
    try:
        return update_blueprint(session, blueprint_id, blueprint)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/blueprints/{blueprint_id}/confirm", response_model=BlueprintCreateRead)
def confirm_saved_blueprint(
    blueprint_id: str, session: Session = Depends(get_session)
) -> BlueprintCreateRead:
    try:
        return confirm_blueprint(session, blueprint_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/papers", response_model=SavedPaperRead)
def create_paper_from_blueprint(
    request: PaperCreateRequest, session: Session = Depends(get_session)
) -> SavedPaperRead:
    try:
        return create_saved_paper(session, request.blueprint_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InfeasiblePaperError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(error),
                "violations": [item.model_dump(mode="json") for item in error.violations],
            },
        ) from error


@router.get("/papers/{paper_id}", response_model=SavedPaperRead)
def read_paper(paper_id: str, session: Session = Depends(get_session)) -> SavedPaperRead:
    try:
        return get_saved_paper(session, paper_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/papers/{paper_id}/validate", response_model=ValidationReportRead)
def validate_paper_record(
    paper_id: str, session: Session = Depends(get_session)
) -> ValidationReportRead:
    try:
        return validate_saved_paper(session, paper_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/papers/{paper_id}/items/{item_id}/replace", response_model=SavedPaperRead)
def replace_saved_paper_item(
    paper_id: str, item_id: str, session: Session = Depends(get_session)
) -> SavedPaperRead:
    try:
        return replace_paper_item(session, paper_id, item_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InfeasiblePaperError as error:
        raise HTTPException(status_code=422, detail={"message": str(error), "violations": [item.model_dump(mode="json") for item in error.violations]}) from error


@router.patch("/papers/{paper_id}/items/{item_id}", response_model=SavedPaperRead)
def patch_saved_paper_item(
    paper_id: str,
    item_id: str,
    request: PaperItemUpdate,
    session: Session = Depends(get_session),
) -> SavedPaperRead:
    try:
        return update_paper_item(session, paper_id, item_id, score=request.score)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/papers/{paper_id}/items/reorder", response_model=SavedPaperRead)
def reorder_saved_paper_items(
    paper_id: str,
    request: PaperReorderRequest,
    session: Session = Depends(get_session),
) -> SavedPaperRead:
    try:
        return reorder_paper_items(session, paper_id, request.item_ids)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/papers/{paper_id}/items/{item_id}/lock", response_model=SavedPaperRead)
def lock_saved_paper_item(
    paper_id: str,
    item_id: str,
    request: PaperLockRequest,
    session: Session = Depends(get_session),
) -> SavedPaperRead:
    try:
        return lock_paper_item(session, paper_id, item_id, locked=request.locked)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/papers/{paper_id}/history", response_model=list[PaperOperationRead])
def read_paper_history(
    paper_id: str, session: Session = Depends(get_session)
) -> list[PaperOperationRead]:
    try:
        return list_paper_history(session, paper_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/papers/{paper_id}/undo", response_model=SavedPaperRead)
def undo_saved_paper(
    paper_id: str,
    request: PaperUndoRequest,
    session: Session = Depends(get_session),
) -> SavedPaperRead:
    try:
        return undo_paper_operations(session, paper_id, count=request.count)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/papers/{paper_id}/redo", response_model=SavedPaperRead)
def redo_saved_paper(
    paper_id: str, session: Session = Depends(get_session)
) -> SavedPaperRead:
    try:
        return redo_paper_operation(session, paper_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/papers/{paper_id}/restore", response_model=SavedPaperRead)
def restore_saved_paper(
    paper_id: str,
    request: PaperRestoreRequest,
    session: Session = Depends(get_session),
) -> SavedPaperRead:
    try:
        return restore_paper_version(session, paper_id, request.version_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except BlueprintStateError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _export_saved_paper(
    paper_id: str, version: str, extension: str, session: Session, settings: Settings
) -> Response:
    if version not in {"student", "teacher"}:
        raise HTTPException(status_code=404, detail="Unknown paper version")
    try:
        preview = load_paper_preview(session, paper_id)
    except WorkflowNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    teacher = version == "teacher"
    if extension == "tex":
        content = render_paper_latex(preview, teacher_version=teacher).encode("utf-8")
        media_type = "application/x-tex; charset=utf-8"
        headers = {}
    else:
        result = build_paper_pdf(
            preview,
            teacher_version=teacher,
            preferred_engine=settings.pdf_engine,
            timeout_seconds=settings.pdf_compile_timeout_seconds,
        )
        content, media_type = result.content, "application/pdf"
        headers = {"X-Paper-Renderer": result.renderer}
    filename = f"{version}-paper.{extension}"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"', **headers})


@router.get("/papers/{paper_id}/exports/{version}.pdf")
def export_saved_pdf(
    paper_id: str,
    version: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _export_saved_paper(paper_id, version, "pdf", session, settings)


@router.get("/papers/{paper_id}/exports/{version}.tex")
def export_saved_tex(
    paper_id: str,
    version: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _export_saved_paper(paper_id, version, "tex", session, settings)


@router.get("/drafts")
def drafts(status: str | None = None, session: Session = Depends(get_session)) -> list[dict]:
    statement = select(QuestionDraft).order_by(QuestionDraft.created_at)
    if status:
        statement = statement.where(QuestionDraft.status == status)
    return [
        {
            "id": item.id,
            "source_item_id": item.source_item_id,
            "variant": item.variant,
            "question_text": item.question_text,
            "reference_answers": item.reference_answers_json,
            "status": item.status,
        }
        for item in session.scalars(statement).all()
    ]


@router.post("/drafts/{draft_id}/process", response_model=DraftProcessRead)
def run_draft(
    draft_id: str,
    solver_mode: str = Query(default="siliconflow", pattern="^(siliconflow|reference)$"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DraftProcessRead:
    draft = session.get(QuestionDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if solver_mode == "reference":
        solver = ReferenceAnswerSolver(draft.reference_answers_json[0])
    else:
        if not settings.siliconflow_api_key:
            raise HTTPException(status_code=503, detail="尚未配置 SiliconFlow API Key")
        solver = OpenAICompatibleSolver(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.siliconflow_agent_model,
            timeout=settings.siliconflow_timeout_seconds,
        )
    try:
        return process_draft(session, draft_id, solver)
    except DraftNotFoundError as error:
        raise HTTPException(status_code=404, detail="Draft not found") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Solver failed: {error}") from error


@router.post("/drafts/{draft_id}/approve", response_model=QuestionRead)
def publish_draft(
    draft_id: str,
    request: DraftApproveRequest,
    session: Session = Depends(get_session),
) -> QuestionRead:
    try:
        return approve_draft(session, draft_id, request)
    except (DraftApprovalError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# --- OCR 端点 ---

from fastapi import File, UploadFile
import asyncio

OCR_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
OCR_PDF_TYPES = {"application/pdf"}


@router.post("/ocr/upload")
async def upload_and_ocr(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    accepted = OCR_IMAGE_TYPES | OCR_PDF_TYPES
    if file.content_type and file.content_type not in accepted:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持图片（JPG/PNG/WebP/BMP）和 PDF，收到：{file.content_type}",
        )
    content = await file.read()
    max_size = 50 * 1024 * 1024 if file.content_type in OCR_PDF_TYPES else 20 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="文件不能超过 50 MB（PDF）/ 20 MB（图片）")
    try:
        task = await create_ocr_task_async(session, content, file.filename or "upload.png")
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"OCR 处理失败：{error}") from error
    return get_ocr_task(session, task.id)


@router.get("/ocr/tasks/{task_id}")
async def read_ocr_task(
    task_id: str, session: Session = Depends(get_session)
) -> dict:
    result = get_ocr_task(session, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="OCR 任务不存在")
    return result


@router.patch("/ocr/blocks/{block_id}")
async def patch_ocr_block(
    block_id: str,
    body: dict,
    session: Session = Depends(get_session),
) -> dict:
    result = update_ocr_block(
        session,
        block_id,
        corrected_text=body.get("corrected_text"),
        corrected_latex=body.get("corrected_latex"),
        review_status=body.get("review_status", "approved"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="OCR 块不存在")
    return result


@router.post("/ocr/tasks/{task_id}/save")
async def save_ocr_review(
    task_id: str,
    body: dict,
    session: Session = Depends(get_session),
) -> dict:
    result = save_ocr_as_draft(
        session,
        task_id,
        merged_text=body.get("merged_text"),
        question_type=body.get("question_type", "计算题"),
        subject=body.get("subject", "高等数学"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="OCR 任务不存在")
    return result


# --- 文档级 OCR（教辅 PDF → 按题切分 → 题库）---

@router.post("/ocr/upload-doc")
async def upload_doc_and_split(
    file: UploadFile = File(...),
    subject: str = "高等数学",
    session: Session = Depends(get_session),
) -> dict:
    """上传教辅 PDF → PPStructureV3 解析 → 按题号切分入库。

    与 /ocr/upload 的区别：
    - /ocr/upload：单图/PDF → PaddleOCR 文字识别 → 逐块审核 → 手动合并
    - /ocr/upload-doc：教辅 PDF → PPStructureV3 版式解析 → 自动按题切分
    """
    if file.content_type and file.content_type not in OCR_PDF_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"upload-doc 仅支持 PDF，收到：{file.content_type}",
        )
    content = await file.read()
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF 不能超过 200 MB")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="上传的 PDF 为空")
    try:
        result = await create_doc_ocr_task_async(
            session, content, file.filename or "document.pdf", subject=subject,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"文档 OCR 处理失败：{error}") from error
    return result


# ── 教材目录管理 ──

DIR_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十\d]+章\s+")
DIR_SECTION_RE = re.compile(r"^(\d+\.\d+)\s+")            # Section: X.Y
DIR_KNOWLEDGE_POINT_RE = re.compile(r"^(\d+\.\d+\.\d+)\s+")  # KnowledgePoint: X.Y.Z
DIR_DEEP_RE = re.compile(r"^(\d+(?:[.]\d+){3,})\s+")      # rejected: X.Y.Z.W+


@router.get("/textbooks")
def list_textbooks(session: Session = Depends(get_session)) -> list[dict]:
    books = session.scalars(select(Textbook).order_by(Textbook.created_at.desc())).all()
    return [
        {"id": b.id, "name": b.name, "edition": b.edition, "description": b.description,
         "is_active": b.is_active, "created_at": b.created_at.isoformat() if b.created_at else None}
        for b in books
    ]


@router.post("/textbooks")
def create_textbook(body: dict, session: Session = Depends(get_session)) -> dict:
    book = Textbook(
        name=body.get("name", "未命名教材"),
        edition=body.get("edition"),
        description=body.get("description"),
    )
    session.add(book)
    session.flush()
    return {"id": book.id, "name": book.name, "edition": book.edition}


@router.post("/textbooks/{textbook_id}/activate")
def activate_textbook(textbook_id: str, session: Session = Depends(get_session)) -> dict:
    book = session.get(Textbook, textbook_id)
    if not book:
        raise HTTPException(status_code=404, detail="教材不存在")
    # 取消所有激活
    for b in session.scalars(select(Textbook).where(Textbook.is_active.is_(True))).all():
        b.is_active = False
    book.is_active = True
    return {"id": book.id, "name": book.name, "is_active": True}


@router.get("/textbooks/{textbook_id}/tree")
def get_textbook_tree(textbook_id: str, session: Session = Depends(get_session)) -> list[dict]:
    nodes = session.scalars(
        select(CurriculumNode)
        .where(CurriculumNode.textbook_id == textbook_id)
        .order_by(CurriculumNode.sort_order)
    ).all()
    node_map: dict[str, dict] = {
        n.id: {"id": n.id, "code": n.code, "title": n.title, "node_type": n.node_type,
               "parent_id": n.parent_id, "children": []}
        for n in nodes
    }
    roots = []
    for n in nodes:
        item = node_map[n.id]
        if n.parent_id and n.parent_id in node_map:
            node_map[n.parent_id]["children"].append(item)
        else:
            roots.append(item)
    return roots


@router.post("/textbooks/{textbook_id}/import")
def import_textbook_directory(textbook_id: str, body: dict, session: Session = Depends(get_session)) -> dict:
    """导入教材目录文本。body: { text: "第一章 ...\n1.1 ...", replace: true }"""
    book = session.get(Textbook, textbook_id)
    if not book:
        raise HTTPException(status_code=404, detail="教材不存在")
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="目录文本不能为空")

    # Confirm re-validates from the raw text (never trusts a frontend-supplied tree).
    strict = bool(body.get("strict"))
    if strict:
        preview_errors, _ = _validate_directory_lines(text, strict=True)
        if preview_errors:
            raise HTTPException(
                status_code=400,
                detail={"valid": False, "errors": preview_errors},
            )

    old_nodes: list[CurriculumNode] = []
    old_knowledge: list[KnowledgeNode] = []
    if body.get("replace"):
        old_nodes = list(session.scalars(
            select(CurriculumNode).where(CurriculumNode.textbook_id == textbook_id)
        ).all())
        old_ids = [node.id for node in old_nodes]
        if old_ids:
            old_knowledge = list(session.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.curriculum_node_id.in_(old_ids),
                    KnowledgeNode.source_type.in_(["directory", "textbook_directory"]),
                )
            ).all())

    nodes = _parse_directory_text(text, textbook_id)
    for n in nodes:
        session.add(n)
    session.flush()
    knowledge = sync_directory_knowledge_nodes(
        session,
        nodes,
        reusable_nodes=old_knowledge,
    )
    if old_nodes:
        keep_ids = {item.id for item in knowledge}
        retire_directory_knowledge_nodes(session, old_knowledge, keep_ids=keep_ids)
        # Child rows must be removed before their former parents.
        old_node_ids = {item.id for item in old_nodes}
        for node in sorted(
            old_nodes,
            key=lambda item: item.parent_id in old_node_ids,
            reverse=True,
        ):
            session.delete(node)
        # First import remains revision 1. Only a successful replacement of an
        # existing directory advances the deterministic directory revision.
        book.directory_revision += 1
        session.flush()
    return {"imported_count": len(nodes), "knowledge_count": len(knowledge)}


@router.post("/textbooks/{textbook_id}/import/preview")
def preview_textbook_directory(
    textbook_id: str, body: dict, session: Session = Depends(get_session)
) -> dict:
    """只读预览：解析 + 严格层级校验 + tree/statistics/errors。不写任何数据库。"""
    book = session.get(Textbook, textbook_id)
    if not book:
        raise HTTPException(status_code=404, detail="教材不存在")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="目录文本不能为空")
    return _preview_directory(textbook_id, text)


def _validate_directory_lines(text: str, *, strict: bool = True) -> tuple[list[dict], dict]:
    """确定性层级校验。纯函数，不依赖数据库。返回 (errors, statistics)。

    strict=True 时，knowledge point 直接挂在 chapter 下（无 section）视为错误；
    三级编号知识点（X.Y.Z）的所属小节前缀必须与当前小节（X.Y）一致，否则报
    knowledge_point_section_mismatch；四级及以上编号（X.Y.Z.W）报
    unsupported_directory_depth。
    strict=False 时仅统计，不报错（兼容首导入历史格式）。

    注：章用中文数字（第X章）、小节用阿拉伯数字（X.Y），二者基数不同，本项目
    暂无可靠的「中文数字→整数」换算，故暂不校验 section_chapter_mismatch。
    """
    errors: list[dict] = []
    stats = {"chapters": 0, "sections": 0, "knowledge_points": 0}
    current_chapter_code: str | None = None
    current_section_code: str | None = None
    current_section_seq = 0
    seen_chapter_codes: set[str] = set()
    seen_section_codes: set[str] = set()
    section_kp_names: dict[int, set[str]] = {}
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        chap = DIR_CHAPTER_RE.match(line)
        kp = DIR_KNOWLEDGE_POINT_RE.match(line)
        sec = DIR_SECTION_RE.match(line)
        deep = DIR_DEEP_RE.match(line)

        # 四级及以上编号不支持
        if deep:
            errors.append({"line": idx, "code": "unsupported_directory_depth",
                           "message": f"出现四级及以上编号“{deep.group(1)}”，本系统仅支持 章/小节/知识点 三级目录"})
            continue
        if chap:
            stats["chapters"] += 1
            num = re.match(r"第([一二三四五六七八九十\d]+)章\s+(.+)", line)
            code = num.group(1) if num else None
            if code:
                if code in seen_chapter_codes:
                    errors.append({"line": idx, "code": "duplicate_chapter_code",
                                   "message": f"章编号“{code}”重复"})
                else:
                    seen_chapter_codes.add(code)
            current_chapter_code = code
            current_section_code = None
            continue
        if kp:
            stats["knowledge_points"] += 1
            kp_code = kp.group(1)              # "3.1.1"
            kp_prefix = ".".join(kp_code.split(".")[:-1])  # "3.1"
            if current_section_code is None:
                errors.append({"line": idx, "code": "knowledge_point_without_section",
                               "message": f"知识点“{line}”没有所属小节，无法确定其目录位置"})
            elif kp_prefix != current_section_code:
                errors.append({"line": idx, "code": "knowledge_point_section_mismatch",
                               "message": f"知识点 {kp_code} 的所属小节编号为 {kp_prefix}，但当前小节为 {current_section_code}"})
            else:
                names = section_kp_names.setdefault(current_section_seq, set())
                if line in names:
                    errors.append({"line": idx, "code": "duplicate_knowledge_point",
                                   "message": f"同一小节下知识点“{line}”重复"})
                else:
                    names.add(line)
            continue
        if sec:
            if current_chapter_code is None:
                errors.append({"line": idx, "code": "section_without_chapter",
                               "message": f"小节“{line}”出现在任何章之前，无法确定所属章"})
            stats["sections"] += 1
            section_code = sec.group(1)
            title = line[sec.end():].strip()
            if not title:
                errors.append({"line": idx, "code": "empty_section_title",
                               "message": f"小节“{line}”标题为空"})
            if section_code in seen_section_codes:
                errors.append({"line": idx, "code": "duplicate_section_code",
                               "message": f"小节编号“{section_code}”重复"})
            else:
                seen_section_codes.add(section_code)
            current_section_code = section_code
            current_section_seq += 1
            section_kp_names[current_section_seq] = set()
            continue
        # 普通非编号知识点行：兼容历史格式
        if strict and current_section_code is None:
            errors.append({"line": idx, "code": "knowledge_point_without_section",
                           "message": f"知识点“{line}”没有所属小节，无法确定其目录位置"})
        if line:
            stats["knowledge_points"] += 1
            if current_section_code is not None:
                names = section_kp_names.setdefault(current_section_seq, set())
                if line in names:
                    errors.append({"line": idx, "code": "duplicate_knowledge_point",
                                   "message": f"同一小节下知识点“{line}”重复"})
                else:
                    names.add(line)
    return errors, stats


def _build_directory_tree(nodes: list[CurriculumNode]) -> list[dict]:
    node_map = {
        n.id: {"id": n.id, "code": n.code, "title": n.title, "type": n.node_type,
               "parent_id": n.parent_id, "children": []}
        for n in nodes
    }
    roots: list[dict] = []
    for n in nodes:
        item = node_map[n.id]
        if n.parent_id and n.parent_id in node_map:
            node_map[n.parent_id]["children"].append(item)
        else:
            roots.append(item)
    return roots


def _preview_directory(textbook_id: str, text: str) -> dict:
    # Parse without touching the session: node ids are assigned but never flushed.
    nodes = _parse_directory_text(text, textbook_id)
    errors, stats = _validate_directory_lines(text, strict=True)
    return {
        "valid": not errors,
        "errors": errors,
        "statistics": stats,
        "tree": _build_directory_tree(nodes) if not errors else None,
    }


def _parse_directory_text(text: str, textbook_id: str) -> list[CurriculumNode]:
    from calculus_agent.models import new_id
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result: list[CurriculumNode] = []
    current_chapter_id: str | None = None
    current_section_id: str | None = None
    order = 0

    for line in lines:
        node_type = "knowledge_point"
        code: str | None = None
        title = line
        # 检查是否是章标题
        chap_match = DIR_CHAPTER_RE.match(line)
        if chap_match:
            node_type = "chapter"
            # 尝试提取编号
            num_match = re.match(r"第([一二三四五六七八九十\d]+)章\s+(.+)", line)
            if num_match:
                code = num_match.group(1)
                title = num_match.group(2) if num_match.group(2).strip() else line
            order += 1
            order = ((order + 9) // 10) * 10
            chapter_id = new_id()
            node = CurriculumNode(
                id=chapter_id, textbook_id=textbook_id, node_type=node_type, code=code or str(order),
                title=title, sort_order=order, source="textbook_import",
            )
            result.append(node)
            current_chapter_id = chapter_id
            current_section_id = None
            continue

        # 三级编号知识点：X.Y.Z（必须在 Section 的 X.Y 之前判定）
        kp_match = DIR_KNOWLEDGE_POINT_RE.match(line)
        if kp_match:
            node_type = "knowledge_point"
            code = kp_match.group(1)
            title = line[kp_match.end():].strip()
            order += 1
            node = CurriculumNode(
                id=new_id(), textbook_id=textbook_id, node_type=node_type, code=code,
                title=title, sort_order=order, source="textbook_import",
                parent_id=current_section_id or current_chapter_id,
            )
            result.append(node)
            continue

        # 检查是否是节标题：X.Y
        sec_match = DIR_SECTION_RE.match(line)
        if sec_match:
            node_type = "section"
            code = sec_match.group(1)
            title = line[sec_match.end():].strip()
            order += 1
            section_id = new_id()
            node = CurriculumNode(
                id=section_id, textbook_id=textbook_id, node_type=node_type, code=code,
                title=title, sort_order=order, source="textbook_import",
                parent_id=current_chapter_id,
            )
            result.append(node)
            current_section_id = section_id
            continue

        # 普通知识点行：挂到最近的节；若无节则退挂到章（兼容历史格式）
        if line:
            order += 1
            node = CurriculumNode(
                id=new_id(), textbook_id=textbook_id, node_type=node_type, code=code,
                title=title, sort_order=order, source="textbook_import",
                parent_id=current_section_id or current_chapter_id,
            )
            result.append(node)

    return result


@router.patch("/textbooks/nodes/{node_id}")
def update_textbook_node(node_id: str, body: dict, session: Session = Depends(get_session)) -> dict:
    node = session.get(CurriculumNode, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    if "title" in body:
        node.title = body["title"]
    if "code" in body:
        node.code = body["code"]
    sync_directory_knowledge_nodes(session, [node])
    return {"id": node.id, "title": node.title, "code": node.code}


@router.delete("/textbooks/nodes/{node_id}")
def delete_textbook_node(node_id: str, session: Session = Depends(get_session)) -> dict:
    node = session.get(CurriculumNode, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    descendants = [node]
    pending = [node.id]
    while pending:
        children = list(session.scalars(
            select(CurriculumNode).where(CurriculumNode.parent_id.in_(pending))
        ).all())
        descendants.extend(children)
        pending = [item.id for item in children]
    descendant_ids = [item.id for item in descendants]
    knowledge = list(session.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.curriculum_node_id.in_(descendant_ids),
            KnowledgeNode.source_type == "directory",
        )
    ).all())
    retire_directory_knowledge_nodes(session, knowledge)
    for item in reversed(descendants):
        session.delete(item)
    return {"deleted": True}


@router.post("/textbooks/nodes")
def add_textbook_node(body: dict, session: Session = Depends(get_session)) -> dict:
    max_order = session.scalar(
        select(func.max(CurriculumNode.sort_order)).where(
            CurriculumNode.textbook_id == body.get("textbook_id")
        )
    ) or 0
    node = CurriculumNode(
        textbook_id=body.get("textbook_id"),
        parent_id=body.get("parent_id"),
        node_type=body.get("node_type", "knowledge_point"),
        code=body.get("code"),
        title=body.get("title", "新节点"),
        sort_order=max_order + 1,
        source="textbook_import",
    )
    session.add(node)
    session.flush()
    sync_directory_knowledge_nodes(session, [node])
    return {"id": node.id, "title": node.title}
