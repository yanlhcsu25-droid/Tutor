from collections.abc import Iterator
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from calculus_agent.config import Settings, get_settings
from calculus_agent.datasets.cmm_math import import_cmm_math
from calculus_agent.datasets.mm_math import import_mm_math
from calculus_agent.datasets.ugmathbench import import_ugmathbench
from calculus_agent.db import build_session_factory
from calculus_agent.knowledge.curriculum import import_curriculum
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.knowledge.retrieval import retrieve_knowledge
from calculus_agent.ocr.import_service import derive_chapter_from_knowledge
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
    Textbook,
    ToolCallTrace,
)
from calculus_agent.orchestration.service import run_paper_agent
from calculus_agent.agent.agent import (
    TeacherAgentResult,
    build_teacher_agent_backend,
    run_teacher_agent,
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
                "question_type": question.question_type,
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
        statement = statement.where(Question.question_type == question_type)
    questions = session.scalars(statement.order_by(Question.created_at.desc()).limit(limit)).all()
    question_ids = [question.id for question in questions]
    knowledge_map = _load_knowledge_names(session, question_ids)
    origin_map = _load_question_origins(session, question_ids)
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
        results.append(
            QuestionOptionRead(
                id=question.id,
                question_text=question.question_text,
                question_type=question.question_type,
                knowledge=knowledge_map.get(question.id, []),
                original_number=original_number,
                source_name=source_name,
                source_page=source_page,
                chapter=session.get(QuestionDraft, question.draft_id).source_topic,
                knowledge_match_status=question.knowledge_match_status,
                difficulty=(profile.difficulty if (profile := latest_profiles.get(question.id)) else None),
                estimated_time_min=profile.estimated_time_min if profile else None,
                reasoning_depth=profile.reasoning_depth if profile else None,
                calculation_load=profile.calculation_load if profile else None,
                comprehensive_level=profile.comprehensive_level if profile else None,
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
    draft.source_topic = derive_chapter_from_knowledge(session, selected_ids)
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
    return QuestionDetailRead(
        id=question.id,
        question_text=question.question_text,
        question_type=question.question_type,
        knowledge=_load_knowledge_names(session, [question.id]).get(question.id, []),
        knowledge_node_ids=_load_knowledge_node_ids(session, question.id),
        solution_content=solution_text or None,
        final_answer=question.final_answer,
        chapter=session.get(QuestionDraft, question.draft_id).source_topic,
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
        question_type=body.get("question_type", "解答题"),
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
DIR_SECTION_RE = re.compile(r"^(\d+(?:[.]\d+)*)\s+")


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
    for b in session.scalars(select(Textbook).where(Textbook.is_active == True)).all():
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

    if body.get("replace"):
        # 删除旧节点
        old_nodes = session.scalars(
            select(CurriculumNode).where(CurriculumNode.textbook_id == textbook_id)
        ).all()
        for n in old_nodes:
            session.delete(n)
        session.flush()

    nodes = _parse_directory_text(text, textbook_id)
    for n in nodes:
        session.add(n)
    session.flush()
    return {"imported_count": len(nodes)}


def _parse_directory_text(text: str, textbook_id: str) -> list[CurriculumNode]:
    from calculus_agent.models import new_id
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result: list[CurriculumNode] = []
    current_chapter_id: str | None = None
    order = 0

    for line in lines:
        node_type = "knowledge_point"
        code: str | None = None
        title = line
        parent_id = None

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
            continue

        # 检查是否是节标题
        sec_match = DIR_SECTION_RE.match(line)
        if sec_match:
            node_type = "section"
            code = sec_match.group(1)
            title = line[sec_match.end():].strip()
            order += 1
            node = CurriculumNode(
                id=new_id(), textbook_id=textbook_id, node_type=node_type, code=code,
                title=title, sort_order=order, source="textbook_import",
                parent_id=current_chapter_id,
            )
            result.append(node)
            continue

        # 普通知识点行
        if line:
            order += 1
            node = CurriculumNode(
                id=new_id(), textbook_id=textbook_id, node_type=node_type, code=code,
                title=title, sort_order=order, source="textbook_import",
                parent_id=current_chapter_id,
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
    return {"id": node.id, "title": node.title, "code": node.code}


@router.delete("/textbooks/nodes/{node_id}")
def delete_textbook_node(node_id: str, session: Session = Depends(get_session)) -> dict:
    node = session.get(CurriculumNode, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    # 递归删除子节点
    children = session.scalars(
        select(CurriculumNode).where(CurriculumNode.parent_id == node_id)
    ).all()
    for c in children:
        session.delete(c)
    session.delete(node)
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
    return {"id": node.id, "title": node.title}
    return result
