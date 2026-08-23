from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pypdfium2 as pdfium
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.db import build_session_factory

from .database import PublishedDraftError, PublishedSourceError, WorkbenchDatabase
from .chapter_filter import (
    chapter_display_label,
    filter_questions_by_chapter,
    list_top_level_chapters,
)
from calculus_agent.knowledge.classification import (
    classify_text_with_llm,
    current_textbook_taxonomy,
)
from .markdown_schema import parse_markdown, payload_from_markdown, render_preview
from .ocr import (
    OCRPipelineError,
    persist_rendered_draft,
    render_drafts,
    run_ocr_into_database,
    trace_split_pages,
)
from .import_pipeline import DocumentLayout, import_document, infer_separate_layout
from .ai_content_review import (
    audit_content_with_llm,
    deterministic_content_issues,
    recommend_difficulty_with_llm,
)
from .resplit import (
    ResplitBlockedError,
    ResplitError,
    ResplitStaleError,
    apply_plan,
    build_plan,
    plan_to_dict,
)


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent.parent  # calculus_knowledge_agent/
DATA_ROOT = PROJECT_ROOT / "workbench_data"
FILES_ROOT = DATA_ROOT / "files"
EXPORT_ROOT = DATA_ROOT / "exports"
PAGE_CACHE_ROOT = DATA_ROOT / "page_cache"
STATIC_ROOT = PACKAGE_ROOT / "static"

for directory in (FILES_ROOT, EXPORT_ROOT, PAGE_CACHE_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="题目校验工作台", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="workbench_static")

# Session factory — 由 main.py 启动时注入
_session_factory = build_session_factory(get_settings().database_url)


@contextmanager
def _get_session() -> Iterator[Session]:
    with _session_factory.begin() as session:
        yield session




# ── request models ──

class MarkdownRequest(BaseModel):
    markdown: str


class SaveRequest(BaseModel):
    markdown: str


class DraftMetadataRequest(BaseModel):
    """结构化元数据保存请求（非 Markdown 载体）。

    knowledge_points 必须是 knowledge_id（UUID），不得传入知识点名称。
    名称仅用于 UI 展示，由前端按 id 反查，不在后端/Markdown 中存储。
    """

    knowledge_points: list[str] = Field(default_factory=list)
    difficulty_level: int | None = Field(default=None, ge=1, le=5)
    content_confirmed: bool | None = None


class HumanKnowledgeReviewRequest(BaseModel):
    primary_knowledge_point_id: str | None = None
    secondary_knowledge_point_ids: list[str] = Field(default_factory=list, max_length=2)
    modification_reason: str | None = Field(default=None, max_length=500)


class PublishedAiProfileReviewRequest(BaseModel):
    primary_knowledge_point_id: str
    secondary_knowledge_point_ids: list[str] = Field(default_factory=list, max_length=2)
    difficulty_level: int = Field(ge=1, le=5)
    modification_reason: str | None = Field(default=None, max_length=500)


class SubmitRequest(BaseModel):
    question_ids: list[str] | None = None


class PublishRequest(BaseModel):
    question_ids: list[str] = Field(min_length=1)


class AutoPublishRequest(BaseModel):
    question_ids: list[str] | None = None


class ResplitRequest(BaseModel):
    """重新切题：markdown 省略时使用库中已保存的 edited_markdown。"""

    markdown: str | None = None
    expected_numbers: list[str] | None = None


class GenerateRequest(BaseModel):
    expected_numbers: list[str] | None = None


class SplitDebugPage(BaseModel):
    page_number: int = Field(ge=1)
    markdown: str


class SplitDebugRequest(BaseModel):
    pages: list[SplitDebugPage] = Field(min_length=1)


_logger = logging.getLogger(__name__)
_ocr_cancel_events: dict[str, threading.Event] = {}
_ocr_delete_requests: set[str] = set()
_ocr_cancel_events_lock = threading.Lock()


def _cancel_event(source_file_id: str) -> threading.Event:
    with _ocr_cancel_events_lock:
        return _ocr_cancel_events.setdefault(source_file_id, threading.Event())


def _discard_cancel_event(source_file_id: str) -> None:
    with _ocr_cancel_events_lock:
        _ocr_cancel_events.pop(source_file_id, None)
        _ocr_delete_requests.discard(source_file_id)


def _delete_requested(source_file_id: str) -> bool:
    with _ocr_cancel_events_lock:
        return source_file_id in _ocr_delete_requests


@app.post("/api/debug/split")
def debug_split(request: SplitDebugRequest) -> dict[str, Any]:
    """返回不含全文正文的切题诊断摘要，供人工核对 OCR 修正结果。"""
    pages = sorted(
        ((page.page_number, page.markdown) for page in request.pages),
        key=lambda item: item[0],
    )
    trace = trace_split_pages(pages)
    return {
        "pages": trace.pages,
        "candidates": trace.candidates,
        "warnings": trace.warnings,
    }


def _safe_id(value: str, prefix: str) -> str:
    prefixed = re.fullmatch(rf"{re.escape(prefix)}_[0-9a-f]{{32}}", value)
    legacy_revision = prefix == "q" and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
    )
    if not prefixed and not legacy_revision:
        raise HTTPException(status_code=400, detail="非法资源ID")
    return value


def _get_db(session: Session) -> WorkbenchDatabase:
    return WorkbenchDatabase(session)




# ── source CRUD ──

def _materialize_display_layout(db: WorkbenchDatabase, source: dict[str, Any]) -> dict[str, Any]:
    """Expose inferred page ranges for older auto-layout sources too."""
    layout = dict(source.get("layout") or {})
    if layout.get("solution_mode") != "separate" or layout.get("question_pages") or layout.get("solution_pages"):
        return source
    try:
        pages = db.list_pages(source["source_file_id"])
        inferred = infer_separate_layout([
            (item["page_number"], item.get("edited_markdown") or item.get("raw_markdown", ""))
            for item in pages
        ])
    except (ValueError, KeyError):
        return source
    layout.update(inferred.to_dict())
    source["layout"] = layout
    return source


@app.get("/api/sources")
def list_sources() -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        return {"items": [_materialize_display_layout(db, item) for item in db.list_sources()]}


@app.post("/api/sources")
async def upload_source(
    file: UploadFile = File(...),
    source_file_id: str | None = Form(None),
    solution_mode: str = Form("inline"),
    ocr_mode: str = Form("mineru"),
    question_page_start: int | None = Form(None),
    question_page_end: int | None = Form(None),
    solution_page_start: int | None = Form(None),
    solution_page_end: int | None = Form(None),
) -> dict[str, Any]:
    filename = Path(file.filename or "document.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="第一版仅支持PDF文件")
    if solution_mode not in {"inline", "separate"}:
        raise HTTPException(status_code=400, detail="不支持的导入模式")
    if ocr_mode not in {"mineru", "ppstructure", "page_recall"}:
        raise HTTPException(status_code=400, detail="不支持的 OCR 方式")
    if solution_mode == "separate":
        # 套卷不再要求人工填写页码；OCR 完成后由 Markdown 内容自动识别答案区。
        # 保留旧字段以兼容旧客户端，但只接受“全部填写”这一旧格式，避免半套范围
        # 与自动识别结果混用。
        values = (question_page_start, question_page_end, solution_page_start, solution_page_end)
        any_range = any(value is not None for value in values)
        if any_range:
            if any(value is None or value < 1 for value in values):
                raise HTTPException(status_code=400, detail="页码范围必须全部填写，或全部留空使用自动识别")
            assert question_page_start is not None and question_page_end is not None
            assert solution_page_start is not None and solution_page_end is not None
            if question_page_start > question_page_end or solution_page_start > solution_page_end:
                raise HTTPException(status_code=400, detail="页码范围起始页不能大于结束页")
            layout = DocumentLayout("separate", list(range(question_page_start, question_page_end + 1)), list(range(solution_page_start, solution_page_end + 1)))
        else:
            layout = DocumentLayout("separate")
    else:
        layout = DocumentLayout(solution_mode="inline")
    source_file_id = source_file_id or f"src_{uuid.uuid4().hex}"
    _safe_id(source_file_id, "src")
    stored_path = FILES_ROOT / f"{source_file_id}.pdf"
    digest = hashlib.sha256()
    size = 0
    with stored_path.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 200 * 1024 * 1024:
                output.close()
                stored_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="PDF不能超过200MB")
            digest.update(chunk)
            output.write(chunk)
    if size == 0:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传的PDF为空")

    # Read the PDF page count before creating the asynchronous OCR job. This
    # is metadata only; OCR still runs page-by-page afterward.
    try:
        pdf_document = pdfium.PdfDocument(str(stored_path))
        actual_page_count = len(pdf_document)
        pdf_document.close()
    except Exception as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"PDF渲染失败：{error}") from error
    if actual_page_count <= 0:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="PDF没有可处理的页面")
    try:
        layout.validate(set(range(1, actual_page_count + 1)))
    except ValueError as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error)) from error

    with _get_session() as session:
        db = _get_db(session)
        initial_layout = layout.to_dict()
        initial_layout["ocr_mode"] = ocr_mode
        initial_layout["progress"] = {
            "current_page": 0, "total_pages": actual_page_count, "status": "queued"
        }
        content_sha256 = digest.hexdigest()
        import_fingerprint = hashlib.sha256(json.dumps(
            {
                "content_sha256": content_sha256,
                "ocr_mode": ocr_mode,
                "layout": layout.to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        source = db.create_source(
            source_file_id,
            filename,
            stored_path,
            content_sha256,
            initial_layout,
            import_fingerprint=import_fingerprint,
        )
        if source["source_file_id"] != source_file_id:
            stored_path.unlink(missing_ok=True)
            source = db.get_source(source["source_file_id"])
            return {
                "source": source,
                "question_count": len(db.list_questions(source["source_file_id"])),
                "deduplicated": True,
            }
        db.update_processing(
            source_file_id, current_page=0, total_pages=actual_page_count, status="ocr"
        )

    cancel_event = _cancel_event(source_file_id)
    cancel_event.clear()

    diagnostics: list[Any] = []

    def run_sync() -> tuple[int, int]:
        with _session_factory() as session:
            db = _get_db(session)

            def report_progress(current_page: int, total_pages: int, status: str) -> None:
                db.update_processing(
                    source_file_id,
                    current_page=current_page,
                    total_pages=total_pages,
                    status=status,
                )
                # OCR pages are persisted incrementally so pausing does not
                # roll back all pages completed earlier in the request.
                session.commit()

            try:
                result = run_ocr_into_database(
                    stored_path,
                    source_file_id,
                    db,
                    raw_root=DATA_ROOT / "ocr_raw" / source_file_id,
                    layout=layout,
                    diagnostics_out=diagnostics,
                    progress_callback=report_progress,
                    cancel_callback=cancel_event.is_set,
                    ocr_mode=ocr_mode,
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    try:
        page_count, question_count = await run_in_threadpool(run_sync)
    except OCRPipelineError as error:
        deleting = _delete_requested(source_file_id)
        paused = cancel_event.is_set()
        with _get_session() as session:
            db = _get_db(session)
            db.finish_source(source_file_id, page_count=actual_page_count, error=str(error))
            db.update_processing(
                source_file_id,
                current_page=error.page_count,
                total_pages=actual_page_count,
                status="deleting" if deleting else ("paused" if paused else "failed"),
                error=str(error),
            )
        if deleting:
            with _get_session() as session:
                deleted = _get_db(session).delete_unpublished_source(source_file_id)
            _cleanup_source_files(source_file_id, deleted["stored_path"])
            raise HTTPException(status_code=410, detail="上传已停止并删除") from error
        if paused:
            raise HTTPException(status_code=409, detail="上传已暂停，已保留完成的 OCR 页面") from error
        raise HTTPException(status_code=422, detail=f"OCR或题目切分失败：{error}") from error
    except Exception as error:
        deleting = _delete_requested(source_file_id)
        with _get_session() as session:
            db = _get_db(session)
            db.finish_source(source_file_id, page_count=actual_page_count, error=str(error))
            db.update_processing(
                source_file_id,
                status="deleting" if deleting else "failed",
                error=str(error),
            )
        if deleting:
            with _get_session() as session:
                deleted = _get_db(session).delete_unpublished_source(source_file_id)
            _cleanup_source_files(source_file_id, deleted["stored_path"])
            raise HTTPException(status_code=410, detail="上传已停止并删除") from error
        raise HTTPException(status_code=422, detail=f"OCR或题目切分失败：{error}") from error
    finally:
        _discard_cancel_event(source_file_id)

    with _get_session() as session:
        db = _get_db(session)
        if layout.solution_mode == "inline":
            layout = DocumentLayout("inline", list(range(1, actual_page_count + 1)), [])
        elif not layout.question_pages and not layout.solution_pages:
            # OCR 阶段已根据 Markdown 完成自动匹配；这里将推断结果保存下来，
            # 供审核界面按“题目/答案”两个页签展示。
            pages = db.list_pages(source_file_id)
            page_inputs = [
                (item["page_number"], item.get("edited_markdown") or item.get("raw_markdown", ""))
                for item in pages
            ]
            layout = infer_separate_layout(page_inputs)
        saved_layout = layout.to_dict()
        if layout.solution_mode == "separate" and layout.solution_pages:
            # 如果答案标题出现在答案起始页的中间，该页在展示上同时属于两侧；
            # 实际匹配仍使用不重叠的 question_pages，避免答案被当成题目。
            first_solution_page = layout.solution_pages[0]
            page = next((text for number, text in page_inputs if number == first_solution_page), "") if 'page_inputs' in locals() else ""
            answer_marker = re.search(r"(?im)^\s*(?:#{1,4}\s*)?(?:参考答案|参考解答|答案与解析|答案解析|试题答案|习题答案|答案|解析|解答)\s*[:：]?\s*$", page)
            question_before_answer = bool(answer_marker and re.search(r"(?m)^\s*\d{1,3}[、.．]", page[:answer_marker.start()]))
            saved_layout["display_question_pages"] = list(layout.question_pages) + ([first_solution_page] if question_before_answer and first_solution_page not in layout.question_pages else [])
            saved_layout["display_solution_pages"] = list(layout.solution_pages)
        saved_layout["ocr_mode"] = ocr_mode
        saved_layout["workflow_stage"] = "markdown_reviewing"
        db.save_source_layout(source_file_id, saved_layout)
        db.finish_source(source_file_id, page_count=actual_page_count)
        db.update_processing(
            source_file_id, current_page=page_count, total_pages=page_count,
            status="completed", question_count=question_count,
        )
        return {
            "source": db.get_source(source_file_id),
            "question_count": question_count,
            "deduplicated": False,
            "import_diagnostics": {
                "unmatched_solutions": [
                    {
                        "section": item.key[0],
                        "original_number": item.key[1],
                        "page_number": item.page_number,
                        "answer": item.answer,
                        "analysis": item.analysis,
                    }
                    for item in (diagnostics[0].unmatched_solutions if diagnostics else [])
                ],
                "ambiguous_keys": diagnostics[0].ambiguous_keys if diagnostics else [],
                "missing_questions": [
                    {
                        "section": item.key[0],
                        "original_number": item.key[1],
                        "page_number": item.page_number,
                    }
                    for item in (diagnostics[0].missing_questions if diagnostics else [])
                ],
                "summary": {
                    "recognized_questions": question_count,
                    "matched": (
                        question_count
                        - len(diagnostics[0].missing_questions if diagnostics else [])
                        - len(diagnostics[0].ambiguous_keys if diagnostics else [])
                    ),
                    "missing_answer": len(diagnostics[0].missing_questions if diagnostics else []),
                    "ambiguous": len(diagnostics[0].ambiguous_keys if diagnostics else []),
                    "extra_answer": len(diagnostics[0].unmatched_solutions if diagnostics else []),
                },
            },
        }


@app.delete("/api/sources/{source_file_id}")
def delete_source(source_file_id: str) -> dict[str, Any]:
    """Stop an active OCR import, or immediately delete an inactive import."""
    _safe_id(source_file_id, "src")
    with _ocr_cancel_events_lock:
        event = _ocr_cancel_events.get(source_file_id)
        if event is not None:
            _ocr_delete_requests.add(source_file_id)
            event.set()
    if event is not None:
        with _get_session() as session:
            try:
                _get_db(session).update_processing(source_file_id, status="deleting")
            except KeyError:
                pass
        return {
            "source_file_id": source_file_id,
            "deleted": False,
            "status": "deleting",
        }
    try:
        with _get_session() as session:
            result = _get_db(session).delete_unpublished_source(source_file_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="找不到源PDF") from error
    except PublishedSourceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    warnings = _cleanup_source_files(source_file_id, result["stored_path"])
    return {
        **{key: value for key, value in result.items() if key != "stored_path"},
        "deleted": True,
        "file_cleanup_warnings": warnings,
    }


def _cleanup_source_files(source_file_id: str, stored_path: str) -> list[str]:
    warnings: list[str] = []
    expected_file = FILES_ROOT / f"{source_file_id}.pdf"
    candidate = Path(stored_path)
    if candidate.resolve() != expected_file.resolve():
        warnings.append("原 PDF 路径不属于该 source 的标准存储位置，已跳过清理")
    else:
        try:
            candidate.unlink(missing_ok=True)
        except OSError as error:
            warnings.append(f"原 PDF 清理失败：{error}")
    for label, path in (
        ("OCR 原文", DATA_ROOT / "ocr_raw" / source_file_id),
        ("页面缓存", PAGE_CACHE_ROOT / source_file_id),
    ):
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError as error:
            warnings.append(f"{label}清理失败：{error}")
    for export in EXPORT_ROOT.glob(f"{source_file_id}_*.jsonl"):
        try:
            export.unlink(missing_ok=True)
        except OSError as error:
            warnings.append(f"审核导出清理失败：{error}")
    return warnings


@app.get("/api/sources/{source_file_id}/questions")
def list_questions(source_file_id: str, chapter_id: str | None = None) -> dict[str, Any]:
    """列出某来源的全部题目；可选 chapter_id 仅返回「派生章节 == chapter_id」的题目。

    chapter_id 为 CurriculumNode.id（一级章节）。不传或传空则不限制章节。
    章节归属与正式题库筛选（search_questions）共用同一确定性规则。
    """
    with _get_session() as session:
        db = _get_db(session)
        source = _source_or_404(db, source_file_id)
        items = db.list_questions(source_file_id)
        if chapter_id:
            # 无效章节 id 时保持不过滤（返回全部题目）。
            items = filter_questions_by_chapter(session, items, chapter_id)
        return {"items": items, "source": source}


@app.get("/api/taxonomy/chapters")
def list_chapters() -> dict[str, Any]:
    """返回当前激活教材的一级章节（大章节）列表，供题库筛选下拉使用。

    仅返回稳定 taxonomy id 与可读标签，不返回整棵知识点树。
    """
    with _get_session() as session:
        chapters = list_top_level_chapters(session)
        return {
            "items": [
                {"id": node.id, "name": chapter_display_label(node)}
                for node in chapters
            ]
        }


@app.get("/api/questions/{question_id}")
def get_question(question_id: str) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        source = _source_or_404(db, question["source_file_id"])
        return {"question": question, "source": _materialize_display_layout(db, source)}


@app.patch("/api/questions/{question_id}")
def save_question(question_id: str, request: SaveRequest) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        _, validation = _validate_question(question, request.markdown)
        try:
            saved = db.save_question(
                question_id, request.markdown, validation.model_dump(mode="json")
            )
        except PublishedDraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"question": saved, "validation": validation.model_dump(mode="json")}


@app.put("/api/questions/{question_id}/metadata")
def update_draft_metadata(question_id: str, request: DraftMetadataRequest) -> dict[str, Any]:
    """保存结构化元数据（知识点名称 / 难度 / 内容确认），不触碰 Markdown。

    知识点以名称保存进 ocr_import_draft.knowledge_points_json，绝不写入 UUID。
    """
    with _get_session() as session:
        db = _get_db(session)
        _question_or_404(db, question_id)
        try:
            saved = db.update_metadata(
                question_id,
                knowledge_points=request.knowledge_points,
                difficulty_level=request.difficulty_level,
                content_confirmed=request.content_confirmed,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="题目不存在")
        except PublishedDraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"question": saved}


@app.put("/api/questions/{question_id}/knowledge/human-review")
def save_human_knowledge_review(
    question_id: str, request: HumanKnowledgeReviewRequest
) -> dict[str, Any]:
    """保存人工知识点真值，同时保留不可变的 AI 推荐快照供准确率分析。"""
    with _get_session() as session:
        db = _get_db(session)
        _question_or_404(db, question_id)
        allowed_ids = {node.id for node in current_textbook_taxonomy(session)}
        selected = ([request.primary_knowledge_point_id] if request.primary_knowledge_point_id else []) + request.secondary_knowledge_point_ids
        if any(item not in allowed_ids for item in selected):
            raise HTTPException(status_code=422, detail="知识点必须属于当前教材的已审核 taxonomy")
        try:
            saved = db.save_human_knowledge_review(
                question_id,
                primary_knowledge_point_id=request.primary_knowledge_point_id,
                secondary_knowledge_point_ids=request.secondary_knowledge_point_ids,
                modification_reason=request.modification_reason,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except PublishedDraftError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"question": saved}


@app.put("/api/questions/{question_id}/ai-published-profile-review")
def review_ai_published_profile(
    question_id: str, request: PublishedAiProfileReviewRequest
) -> dict[str, Any]:
    """人工复核 AI 已发布题的知识点与难度，不解封或改写题目正文。"""
    from calculus_agent.models import OcrImportDraft
    from calculus_agent.ocr.import_service import apply_ai_published_profile_review

    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        if (
            question.get("review_status") != "published"
            or question.get("publish_source") != "ai_auto"
        ):
            raise HTTPException(status_code=409, detail="仅 AI 自动发布题支持此复核入口")
        selected = [
            request.primary_knowledge_point_id,
            *request.secondary_knowledge_point_ids,
        ]
        if len(selected) != len(set(selected)):
            raise HTTPException(status_code=422, detail="主知识点与辅助知识点不能重复")
        allowed_ids = {node.id for node in current_textbook_taxonomy(session)}
        if any(item not in allowed_ids for item in selected):
            raise HTTPException(
                status_code=422,
                detail="知识点必须属于当前教材的已审核 taxonomy",
            )
        draft = session.get(OcrImportDraft, question_id)
        try:
            apply_ai_published_profile_review(
                session,
                draft,
                primary_knowledge_point_id=request.primary_knowledge_point_id,
                secondary_knowledge_point_ids=request.secondary_knowledge_point_ids,
                difficulty_level=request.difficulty_level,
                modification_reason=request.modification_reason,
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"question": _question_or_404(db, question_id)}


@app.post("/api/questions/{question_id}/revision")
def create_question_revision(question_id: str) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        try:
            revision = db.create_revision(question_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="题目不存在")
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return {"question": revision, "source": db.get_source(revision["source_file_id"])}


@app.post("/api/questions/{question_id}/validate")
def validate_question(question_id: str, request: SaveRequest) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        _, validation = _validate_question(question, request.markdown)
        return validation.model_dump(mode="json")


@app.post("/api/preview")
def preview(request: MarkdownRequest) -> dict[str, Any]:
    rendered, issues = render_preview(request.markdown)
    return {
        "html": rendered,
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }


@app.post("/api/questions/{question_id}/diff", response_class=HTMLResponse)
def question_diff(question_id: str, request: MarkdownRequest) -> str:
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        return difflib.HtmlDiff(wrapcolumn=72).make_table(
            question["ocr_markdown"].splitlines(),
            request.markdown.splitlines(),
            fromdesc="OCR原文",
            todesc="人工修改",
            context=True,
            numlines=3,
        )


@app.get("/api/sources/{source_file_id}/pdf")
def source_pdf(source_file_id: str) -> FileResponse:
    with _get_session() as session:
        source = _source_or_404(_get_db(session), source_file_id)
        return FileResponse(source["stored_path"], media_type="application/pdf")


@app.get("/api/sources/{source_file_id}/pages/{page_number}")
def render_page(source_file_id: str, page_number: int, scale: float = 1.6) -> Response:
    with _get_session() as session:
        source = _source_or_404(_get_db(session), source_file_id)
        if page_number < 1 or page_number > source["page_count"]:
            raise HTTPException(status_code=404, detail="页码超出范围")
        scale = max(0.8, min(scale, 3.0))
        cache_dir = PAGE_CACHE_ROOT / source_file_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"page_{page_number:04d}_{scale:.1f}.png"
        if not cache_path.is_file():
            document = pdfium.PdfDocument(source["stored_path"])
            page = document[page_number - 1]
            bitmap = page.render(scale=scale)
            bitmap.to_pil().save(cache_path, format="PNG")
            page.close()
            document.close()
        return FileResponse(cache_path, media_type="image/png")


# ── 整页 Markdown / 重新切题 ──

@app.get("/api/sources/{source_file_id}/pages/{page_number}/markdown")
def get_page_markdown(source_file_id: str, page_number: int) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        _backfill_pages(db, source_file_id)
        try:
            page = db.get_page(source_file_id, page_number)
        except KeyError as error:
            raise HTTPException(
                status_code=404,
                detail=f"第 {page_number} 页没有整页 Markdown 记录（该 PDF 可能在此功能上线前导入且原始文件已清理）",
            ) from error
        page["drafts"] = db.list_questions_by_pages(source_file_id, [page_number])
        return page


@app.put("/api/sources/{source_file_id}/pages/{page_number}/markdown")
def save_page_markdown(
    source_file_id: str, page_number: int, request: MarkdownRequest
) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        _backfill_pages(db, source_file_id)
        try:
            return db.save_page_markdown(source_file_id, page_number, request.markdown)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="找不到该页") from error


@app.post("/api/sources/{source_file_id}/pages/{page_number}/markdown/restore")
def restore_page_markdown(source_file_id: str, page_number: int) -> dict[str, Any]:
    """只恢复 edited_markdown，不触发 OCR 或切题。"""
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        try:
            return db.restore_page_markdown(source_file_id, page_number)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="找不到该页") from error


def _source_generation_plan(db: WorkbenchDatabase, source_file_id: str) -> dict[str, Any]:
    """从已保存的 edited Markdown 生成整份 source 的 parser/matcher 预览。

    这里明确不调用 OCR；OCR 只发生在 upload_source 的后台任务中。
    """
    source = db.get_source(source_file_id)
    pages = db.list_pages(source_file_id)
    if not pages:
        raise ResplitError("该来源尚未保存整页 Markdown，无法生成题目。")
    available = [item["page_number"] for item in pages]
    layout = DocumentLayout.from_dict(source.get("layout"), available_pages=available)
    layout.validate(set(available))
    inputs = [(item["page_number"], item["edited_markdown"] or item["raw_markdown"])
              for item in pages]
    result = import_document(inputs, layout)
    rendered = []
    for placed in result.candidates:
        rendered.extend(render_drafts(placed))
    old = db.list_questions(source_file_id)
    published = [item for item in old if item["review_status"] == "published"]
    published_keys = {
        (item["page_number"], item["original_number"]) for item in published
    }

    # “整页 Markdown”仍需参与整份文档的 parser/matcher，才能正确识别题目与答案；
    # 但生成阶段必须把已发布题对应的结果排除，避免覆盖正式题或创建同键副本。
    # 与已发布题同键的未发布记录通常是人工修订草稿，也一并保留。
    protected_rendered = [
        item
        for item in rendered
        if (item.page_number, item.original_number) in published_keys
    ]
    rebuildable_rendered = [
        item
        for item in rendered
        if (item.page_number, item.original_number) not in published_keys
    ]
    unpublished = [item for item in old if item["review_status"] != "published"]
    protected_unpublished = [
        item
        for item in unpublished
        if (item["page_number"], item["original_number"]) in published_keys
    ]
    rebuildable_unpublished = [
        item
        for item in unpublished
        if (item["page_number"], item["original_number"]) not in published_keys
    ]
    return {
        "source_file_id": source_file_id,
        "layout": layout.to_dict(),
        "new_numbers": [item.original_number for item in rebuildable_rendered],
        "created": [{"page_number": item.page_number, "original_number": item.original_number,
                     "match_status": item.match_status, "review_note": item.review_note,
                     "preview": " ".join(item.markdown.split())[:160]}
                    for item in rebuildable_rendered],
        "old_unpublished": rebuildable_unpublished,
        "preserved_published": published,
        "preserved_unpublished": protected_unpublished,
        "excluded_published_results": len(protected_rendered),
        # 兼容旧前端字段。已发布题现在会被排除而不是阻塞整次重建。
        "published_conflicts": [],
        "diagnostics": {
            "ambiguous_keys": result.diagnostics.ambiguous_keys,
            "missing_questions": [item.key[1] for item in result.diagnostics.missing_questions],
            "unmatched_solutions": [item.key[1] for item in result.diagnostics.unmatched_solutions],
        },
        "blocked": False,
        "_rendered": rebuildable_rendered,
    }


@app.post("/api/sources/{source_file_id}/generate/preview")
def generate_preview(source_file_id: str) -> dict[str, Any]:
    with _get_session() as session:
        try:
            plan = _source_generation_plan(_get_db(session), source_file_id)
            plan.pop("_rendered", None)
            return plan
        except (KeyError, ResplitError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            logging.exception("生成题目预览失败 source=%s", source_file_id)
            raise HTTPException(status_code=500, detail=f"生成题目预览失败：{type(error).__name__}: {error}") from error


@app.post("/api/sources/{source_file_id}/generate/apply")
def generate_apply(source_file_id: str, request: GenerateRequest) -> dict[str, Any]:
    """用最新 edited Markdown 替换未发布切题结果；不重新 OCR。"""
    with _get_session() as session:
        db = _get_db(session)
        try:
            plan = _source_generation_plan(db, source_file_id)
            if request.expected_numbers is not None and request.expected_numbers != plan["new_numbers"]:
                raise ResplitStaleError(request.expected_numbers, plan["new_numbers"])
            if plan["blocked"]:
                raise ResplitBlockedError(plan["published_conflicts"])
            db.delete_questions([item["question_id"] for item in plan["old_unpublished"]])
            created = [persist_rendered_draft(db, source_file_id=source_file_id, draft=item)
                       for item in plan["_rendered"]]
            source = db.get_source(source_file_id)
            layout = dict(source.get("layout") or {})
            layout["workflow_stage"] = "matching_review"
            db.save_source_layout(source_file_id, layout)
            return {"source_file_id": source_file_id, "created_question_ids": created,
                    "created_count": len(created), "deleted_count": len(plan["old_unpublished"]),
                    "preserved_published_count": len(plan["preserved_published"]),
                    "preserved_unpublished_count": len(plan["preserved_unpublished"]),
                    "new_numbers": plan["new_numbers"], "diagnostics": plan["diagnostics"]}
        except ResplitStaleError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ResplitBlockedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (KeyError, ResplitError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            logging.exception("生成题目失败 source=%s", source_file_id)
            raise HTTPException(status_code=500, detail=f"生成题目失败：{type(error).__name__}: {error}") from error


@app.post("/api/sources/{source_file_id}/answers/repair")
def repair_missing_answers(source_file_id: str) -> dict[str, Any]:
    """用当前答案页重新匹配，只补空答案，不覆盖人工内容或已发布题。"""
    with _get_session() as session:
        db = _get_db(session)
        try:
            plan = _source_generation_plan(db, source_file_id)
        except (KeyError, ResplitError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        fresh_by_key: dict[tuple[int, str], list[Any]] = {}
        for draft in plan["_rendered"]:
            fresh_by_key.setdefault(
                (draft.page_number, draft.original_number), []
            ).append(draft)

        repaired_ids: list[str] = []
        for current in db.list_questions(source_file_id):
            if current["review_status"] == "published":
                continue
            # 旧版本可能已补回答案并标记 matched，却遗漏清除 Markdown
            # 中的 answer_not_found。这类历史残留无需重新依赖候选映射。
            if db.clear_stale_answer_not_found_note(current["question_id"]):
                repaired_ids.append(current["question_id"])
                continue
            candidates = fresh_by_key.get(
                (current["page_number"], current["original_number"]), []
            )
            if len(candidates) != 1 or candidates[0].match_status != "matched":
                continue
            candidate = candidates[0]
            solution = parse_markdown(candidate.markdown).sections.get(
                "参考解答", ""
            )
            if db.repair_missing_answer(
                current["question_id"],
                solution_content=solution,
                match_method=candidate.match_method,
            ):
                repaired_ids.append(current["question_id"])

        return {
            "source_file_id": source_file_id,
            "repaired_count": len(repaired_ids),
            "repaired_question_ids": repaired_ids,
        }


@app.post("/api/sources/{source_file_id}/pages/{page_number}/resplit/preview")
def resplit_preview(
    source_file_id: str, page_number: int, request: ResplitRequest
) -> dict[str, Any]:
    """只读预览：返回受影响页区间、旧草稿、新候选与差异。不写数据库。"""
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        _backfill_pages(db, source_file_id)
        markdown = _resolve_page_markdown(db, source_file_id, page_number, request)
        try:
            return plan_to_dict(build_plan(db, source_file_id, page_number, markdown))
        except ResplitError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/sources/{source_file_id}/pages/{page_number}/resplit/apply")
def resplit_apply(
    source_file_id: str, page_number: int, request: ResplitRequest
) -> dict[str, Any]:
    """确认重建：删除受影响范围内的旧未发布草稿并按新切题结果重建。

    整个删除 + 插入 + 保存整页 Markdown 在同一个事务内完成。
    """
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        _backfill_pages(db, source_file_id)
        markdown = _resolve_page_markdown(db, source_file_id, page_number, request)
        try:
            result = apply_plan(
                db,
                source_file_id,
                page_number,
                markdown,
                expected_numbers=request.expected_numbers,
            )
        except ResplitBlockedError as error:
            raise HTTPException(
                status_code=409,
                detail=str(error),
            ) from error
        except ResplitStaleError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ResplitError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        result["questions"] = db.list_questions(source_file_id)
        return result


# ── submit / validate ──

@app.post("/api/sources/{source_file_id}/submit")
def submit_questions(source_file_id: str, request: SubmitRequest) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        questions = db.list_questions(source_file_id)
        selected = set(request.question_ids or [item["question_id"] for item in questions])
        questions = [item for item in questions if item["question_id"] in selected]
        missing = selected - {item["question_id"] for item in questions}
        failures: list[dict[str, Any]] = [
            {"question_id": question_id, "reasons": ["题目不属于当前PDF或不存在"]}
            for question_id in sorted(missing)
        ]
        imported: list[dict[str, Any]] = []
        for question in questions:
            # 发布封板：已发布的草稿不再参与提交/回写，避免被退回 in_review
            if question["review_status"] == "published":
                continue
            if not question.get("content_confirmed"):
                failures.append({"question_id": question["question_id"], "reasons": ["请先确认题目内容"]})
                continue
            metadata_issues = []
            if question.get("difficulty_level") not in {1, 2, 3, 4, 5}:
                metadata_issues.append("difficulty_level 必须由人工填写为 1～5")
            points = question.get("knowledge_points") or []
            if not 1 <= len(points) <= 3:
                metadata_issues.append("知识点必须人工确认 1～3 个")
            if question.get("match_status") in {"missing_answer", "ambiguous", "unknown"}:
                metadata_issues.append(
                    "参考解答匹配异常："
                    + (question.get("review_note") or question.get("match_status"))
                )
            if metadata_issues:
                failures.append({"question_id": question["question_id"], "reasons": metadata_issues})
                continue
            payload, validation = _validate_question(question)
            if payload is None:
                db.save_question(
                    question["question_id"],
                    question["edited_markdown"],
                    validation.model_dump(mode="json"),
                )
                failures.append({
                    "question_id": question["question_id"],
                    "reasons": [f"{issue.field}: {issue.message}" for issue in validation.issues],
                    "issues": [issue.model_dump(mode="json") for issue in validation.issues],
                })
                continue
            if not payload.solution_content.strip():
                failures.append({
                    "question_id": question["question_id"],
                    "reasons": ["参考解答：未识别到参考解答内容，请核对答案页或在 Markdown 源码中补充"],
                })
                continue
            # 结构化元数据以数据库字段为事实来源，不被被反复修改的 Markdown 覆盖。
            payload.knowledge_points = list(question.get("knowledge_points") or [])
            payload.difficulty = question.get("difficulty_level")
            # 校验通过后必须推进状态，否则前端进度统计仍会显示为 0%。
            db.mark_reviewed(
                question["question_id"],
                validation.model_dump(mode="json"),
            )
            imported.append(payload.model_dump(mode="json"))

        export_path: Path | None = None
        if imported:
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            export_path = EXPORT_ROOT / f"{source_file_id}_{timestamp}.jsonl"
            export_path.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in imported),
                encoding="utf-8",
            )
        return {
            "success_count": len(imported),
            "already_imported_count": 0,
            "failure_count": len(failures),
            "failures": failures,
            "jsonl_path": str(export_path) if export_path else None,
        }


@app.get("/api/questions/{question_id}/knowledge/options")
def knowledge_options(question_id: str) -> dict[str, Any]:
    """只读：返回当前激活教材目录下已审核通过的知识点（knowledge_id + name）。

    供前端在加载题目后按已保存的 knowledge_id 反显名称，不触发 AI 分类、不要求内容确认。
    """
    with _get_session() as session:
        _question_or_404(_get_db(session), question_id)
        nodes = current_textbook_taxonomy(session)
        options = [{"knowledge_id": node.id, "name": node.name} for node in nodes]
        return {"question_id": question_id, "options": options}


@app.post("/api/questions/{question_id}/knowledge/classify")
def classify_workbench_question(question_id: str) -> dict[str, Any]:
    """Return a recommendation only; this endpoint never writes formal links."""
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        if not question.get("content_confirmed"):
            raise HTTPException(status_code=409, detail="请先确认题目内容")
        # 同一份待审核内容只保留第一份 AI 建议，避免重新请求破坏 Shadow 基线。
        shadow = question.get("knowledge_shadow") or {}
        saved_ai = shadow.get("ai") if isinstance(shadow, dict) else None
        taxonomy_nodes = current_textbook_taxonomy(session)
        legal_ids = {node.id for node in taxonomy_nodes}
        saved_ids = {
            item
            for item in [
                saved_ai.get("primary_knowledge_point_id") if isinstance(saved_ai, dict) else None,
                *((saved_ai.get("secondary_knowledge_point_ids") or []) if isinstance(saved_ai, dict) else []),
            ]
            if item
        }
        stale_taxonomy = bool(saved_ids - legal_ids)
        payload, _validation = payload_from_markdown(
            question["edited_markdown"],
            question_id=question["question_id"],
            source_file_id=question["source_file_id"],
            ocr_markdown=question["ocr_markdown"],
            source_bbox=question.get("source_bbox"),
        )
        question_body = payload.question_content if payload else question["edited_markdown"]
        standard_solution = payload.solution_content if payload else ""
        has_human_review = isinstance(shadow.get("human"), dict) if isinstance(shadow, dict) else False
        should_retry_fallback = (
            isinstance(saved_ai, dict)
            and (
                stale_taxonomy
                or (
                    saved_ai.get("provenance") != "llm_suggested"
                    and not has_human_review
                )
            )
        )
        generated_knowledge = False
        if isinstance(saved_ai, dict) and not should_retry_fallback:
            result = dict(saved_ai)
        else:
            result = classify_text_with_llm(
                session,
                question_body=question_body,
                standard_solution=standard_solution,
                solution_steps=[],
            )
            generated_knowledge = True

        knowledge_ids = [
            item
            for item in [
                result.get("primary_knowledge_point_id"),
                *(result.get("secondary_knowledge_point_ids") or []),
            ]
            if item
        ]
        difficulty_result = result.get("difficulty_result")
        should_generate_difficulty = (
            knowledge_ids
            and (
                not isinstance(difficulty_result, dict)
                or difficulty_result.get("provenance") != "llm_suggested"
            )
        )
        if should_generate_difficulty:
            difficulty_result = recommend_difficulty_with_llm(
                session,
                question_body=question_body,
                standard_solution=standard_solution,
                question_type=payload.question_type if payload else "unknown",
                knowledge_ids=knowledge_ids,
            )
            result = {**result, "difficulty_result": difficulty_result}

        if generated_knowledge:
            question = db.save_knowledge_shadow(
                question_id,
                result,
                replace_fallback=should_retry_fallback,
                replace_stale_taxonomy=stale_taxonomy,
            )
        elif should_generate_difficulty and isinstance(difficulty_result, dict):
            question = db.save_ai_difficulty_shadow(
                question_id,
                difficulty_result,
            )
        options = [{"knowledge_id": node.id, "name": node.name} for node in taxonomy_nodes]
        selected = []
        if result["primary_knowledge_point"]:
            selected.append({
                **result["primary_knowledge_point"],
                "confidence": result["confidence"],
                "role": "primary",
            })
        selected.extend({
            **item,
            "confidence": result["confidence"],
            "role": "secondary",
        } for item in result["secondary_knowledge_points"])
        return {
            "question_id": question_id,
            **result,
            "knowledge_points": selected,
            "options": options,
            "difficulty_result": difficulty_result,
            "knowledge_shadow": question.get("knowledge_shadow"),
        }


@app.get("/api/knowledge/shadow/stats")
def knowledge_shadow_stats() -> dict[str, Any]:
    """基于教师已确认的真实题库审核记录统计 AI 推荐质量。"""
    from calculus_agent.models import OcrImportDraft

    with _get_session() as session:
        rows = session.query(OcrImportDraft).filter(OcrImportDraft.knowledge_shadow_json.is_not(None)).all()
        reviewed = [row.knowledge_shadow_json for row in rows if isinstance(row.knowledge_shadow_json, dict) and isinstance(row.knowledge_shadow_json.get("human"), dict)]
        primary_correct = 0
        modified = 0
        secondary_tp = secondary_pred = secondary_gold = 0
        high_conf_total = high_conf_wrong = 0
        needs_review_total = needs_review_modified = 0
        for item in reviewed:
            ai, human = item["ai"], item["human"]
            same_primary = ai.get("primary_knowledge_point_id") == human.get("primary_knowledge_point_id")
            primary_correct += int(same_primary)
            changed = bool(human.get("modified"))
            modified += int(changed)
            predicted, expected = set(ai.get("secondary_knowledge_point_ids") or []), set(human.get("secondary_knowledge_point_ids") or [])
            secondary_tp += len(predicted & expected)
            secondary_pred += len(predicted)
            secondary_gold += len(expected)
            if float(ai.get("confidence") or 0) >= 0.85:
                high_conf_total += 1
                high_conf_wrong += int(changed)
            if ai.get("needs_review"):
                needs_review_total += 1
                needs_review_modified += int(changed)
        total = len(reviewed)
        return {
            "total_ai_recommendations": len(rows), "reviewed_total": total,
            "primary_accuracy": primary_correct / total if total else None,
            "secondary_precision": secondary_tp / secondary_pred if secondary_pred else None,
            "secondary_recall": secondary_tp / secondary_gold if secondary_gold else None,
            "human_modification_rate": modified / total if total else None,
            "high_confidence_error_rate": high_conf_wrong / high_conf_total if high_conf_total else None,
            "needs_review_modified_rate": needs_review_modified / needs_review_total if needs_review_total else None,
        }


@app.post("/api/questions/{question_id}/confirm-content")
def confirm_question_content(question_id: str) -> dict[str, Any]:
    with _get_session() as session:
        try:
            return _get_db(session).confirm_content(question_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="题目不存在") from error
        except PublishedDraftError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


# ── publish ──

@app.post("/api/publish")
def publish_questions(request: PublishRequest) -> dict[str, Any]:
    """将已审核的 OCR 草稿发布到 MathPaper-Agent 正式题库。

    使用 QuestionImportService，流程：
    OcrImportDraft → 解析 Markdown → QuestionDraft → Question → 组卷可用
    """
    from calculus_agent.ocr.import_service import publish_ocr_draft
    from calculus_agent.models import OcrImportDraft

    published = 0
    failures: list[dict[str, str]] = []
    synced: list[dict[str, str]] = []

    with _get_session() as session:
        for question_id in request.question_ids:
            try:
                draft = session.get(OcrImportDraft, question_id)
                if draft is None:
                    failures.append({"question_id": question_id, "reason": "草稿不存在"})
                    continue

                if draft.review_status not in ("in_review", "reviewed"):
                    failures.append({
                        "question_id": question_id,
                        "reason": f"草稿状态为 {draft.review_status}，需要先保存审核",
                    })
                    continue

                # 题目与答案未配对成功（缺答案 / 题号冲突）或历史状态未知（unknown）
                # 禁止发布：缺答案可人工补全，unknown 须重新切题/匹配确认后才可发布。
                if getattr(draft, "match_status", "matched") in ("missing_answer", "ambiguous", "unknown"):
                    failures.append({
                        "question_id": question_id,
                        "reason": f"题目答案配对状态未确认（{draft.match_status}），请重新切题/匹配或补全答案后再发布",
                    })
                    continue

                result = publish_ocr_draft(session, draft)
                if result is None:
                    failures.append({
                        "question_id": question_id,
                        "reason": "题目已存在或无法发布",
                    })
                    continue

                published += 1
                synced.append({
                    "question_id": question_id,
                    "bank_draft_id": result["draft_id"],
                    "bank_question_id": result["question_id"],
                    "cached": result.get("cached", False),
                })
            except Exception as exc:
                _logger.warning("发布题目失败 question_id=%s: %s", question_id, exc)
                failures.append({"question_id": question_id, "reason": str(exc)})

    return {
        "published_count": published,
        "failure_count": len(failures),
        "failures": failures,
        "synced_to_bank": len(synced),
        "sync_details": synced,
    }


@app.post("/api/sources/{source_file_id}/ai-auto-publish")
def ai_auto_publish(
    source_file_id: str,
    request: AutoPublishRequest,
) -> dict[str, Any]:
    """受控自动发布：硬校验、AI 语义审核、知识点和画像全部通过才发布。"""
    from calculus_agent.models import OcrImportDraft, Question
    from calculus_agent.ocr.import_service import publish_ocr_draft

    published: list[str] = []
    manual_review: list[dict[str, Any]] = []
    sampled: list[str] = []
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        questions = db.list_questions(source_file_id)
        selected_ids = set(request.question_ids or [item["question_id"] for item in questions])
        questions = [item for item in questions if item["question_id"] in selected_ids]
        already_ai_published = int(
            session.scalar(
                select(func.count())
                .select_from(Question)
                .where(Question.publish_source == "ai_auto")
            )
            or 0
        )

        for question in questions:
            question_id = question["question_id"]
            if question["review_status"] == "published":
                continue
            hard_issues, payload = deterministic_content_issues(question)
            if hard_issues or payload is None:
                audit = _blocked_ai_audit("deterministic_check_failed", hard_issues)
                db.save_ai_content_review(question_id, audit, passed=False)
                manual_review.append({"question_id": question_id, "reasons": hard_issues})
                continue

            audit = audit_content_with_llm(
                question_body=payload.question_content,
                standard_solution=payload.solution_content,
                question_type=payload.question_type,
            )
            if not audit["passed"]:
                db.save_ai_content_review(question_id, audit, passed=False)
                manual_review.append({
                    "question_id": question_id,
                    "reasons": audit.get("risk_codes") or [audit.get("reason")],
                })
                continue

            knowledge = classify_text_with_llm(
                session,
                question_body=payload.question_content,
                standard_solution=payload.solution_content,
                solution_steps=[],
            )
            knowledge_ids = [
                item
                for item in [
                    knowledge.get("primary_knowledge_point_id"),
                    *(knowledge.get("secondary_knowledge_point_ids") or []),
                ]
                if item
            ]
            if (
                knowledge.get("provenance") != "llm_suggested"
                or knowledge.get("needs_review")
                or not knowledge.get("primary_knowledge_point_id")
                or not 1 <= len(knowledge_ids) <= 3
            ):
                failed_audit = {
                    **audit,
                    "passed": False,
                    "verdict": "REVIEW",
                    "risk_codes": ["knowledge_classification_failed"],
                    "reason": "知识点 AI 推荐未形成可自动发布的合法结果",
                    "knowledge_result": knowledge,
                }
                db.save_ai_content_review(question_id, failed_audit, passed=False)
                manual_review.append({
                    "question_id": question_id,
                    "reasons": ["knowledge_classification_failed"],
                })
                continue

            difficulty_result = recommend_difficulty_with_llm(
                session,
                question_body=payload.question_content,
                standard_solution=payload.solution_content,
                question_type=payload.question_type,
                knowledge_ids=knowledge_ids,
            )
            difficulty = difficulty_result.get("difficulty_level")
            if (
                difficulty_result.get("provenance") != "llm_suggested"
                or difficulty_result.get("needs_review")
                or difficulty not in {1, 2, 3, 4, 5}
            ):
                failed_audit = {
                    **audit,
                    "passed": False,
                    "verdict": "REVIEW",
                    "risk_codes": ["difficulty_classification_failed"],
                    "reason": "AI 难度推荐未形成可自动发布的合法结果",
                    "knowledge_result": knowledge,
                    "difficulty_result": difficulty_result,
                    "difficulty_level": difficulty,
                }
                db.save_ai_content_review(question_id, failed_audit, passed=False)
                manual_review.append({
                    "question_id": question_id,
                    "reasons": ["difficulty_classification_failed"],
                })
                continue

            sample_required = (
                already_ai_published + len(published) < 200
                and int(hashlib.sha256(question_id.encode()).hexdigest()[:8], 16) % 5 == 0
            )
            full_audit = {
                **audit,
                "knowledge_result": knowledge,
                "difficulty_result": difficulty_result,
                "difficulty_level": difficulty,
                "publish_source": "ai_auto",
                "quality_sample_required": sample_required,
            }
            db.save_knowledge_shadow(question_id, knowledge)
            db.save_ai_content_review(
                question_id,
                full_audit,
                passed=True,
                knowledge_ids=knowledge_ids,
                difficulty_level=difficulty,
                quality_sample_required=sample_required,
            )
            draft = session.get(OcrImportDraft, question_id)
            try:
                with session.begin_nested():
                    result = publish_ocr_draft(
                        session,
                        draft,
                        publish_source="ai_auto",
                        ai_review_result=full_audit,
                        quality_sample_required=sample_required,
                    )
            except Exception:
                _logger.exception("AI 自动发布失败 question_id=%s", question_id)
                failed_audit = {
                    **full_audit,
                    "passed": False,
                    "verdict": "REVIEW",
                    "risk_codes": ["publish_error"],
                    "reason": "正式发布失败，已转人工处理",
                }
                db.save_ai_content_review(question_id, failed_audit, passed=False)
                manual_review.append({"question_id": question_id, "reasons": ["publish_error"]})
                continue
            if result is None:
                failed_audit = {
                    **full_audit,
                    "passed": False,
                    "verdict": "REVIEW",
                    "risk_codes": ["publish_error"],
                    "reason": "正式发布未产生题库记录，已转人工处理",
                }
                db.save_ai_content_review(question_id, failed_audit, passed=False)
                manual_review.append({"question_id": question_id, "reasons": ["publish_error"]})
                continue
            published.append(question_id)
            if sample_required:
                sampled.append(question_id)

    return {
        "eligible_count": len(questions),
        "published_count": len(published),
        "published_question_ids": published,
        "manual_review_count": len(manual_review),
        "manual_review": manual_review,
        "quality_sample_count": len(sampled),
        "quality_sample_question_ids": sampled,
    }


def _blocked_ai_audit(reason: str, issues: list[str]) -> dict[str, Any]:
    return {
        "verdict": "REVIEW",
        "answer_relevant": False,
        "conclusion_consistent": False,
        "no_cross_question": False,
        "derivation_complete": False,
        "confidence": 0.0,
        "risk_codes": issues or [reason],
        "reason": "确定性发布条件未全部通过，必须人工检查",
        "passed": False,
        "fallback_reason": reason,
        "raw_response_type": None,
        "model": None,
    }


# ── home ──

@app.get("/", response_class=HTMLResponse)
def home() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


# ── helpers ──

def _source_or_404(db: WorkbenchDatabase, source_file_id: str) -> dict[str, Any]:
    _safe_id(source_file_id, "src")
    try:
        return db.get_source(source_file_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="找不到源PDF") from error


def _backfill_pages(db: WorkbenchDatabase, source_file_id: str) -> None:
    """存量来源补录整页 Markdown。

    ocr_page 表上线前导入的 PDF 在库里没有整页记录，这里一次性从
    workbench_data/ocr_raw/<source_id>/page_NNNN.md 回填。回填之后
    磁盘文件不再是数据源，后续一律以数据库为准。
    """
    if db.list_pages(source_file_id):
        return
    raw_root = DATA_ROOT / "ocr_raw" / source_file_id
    if not raw_root.is_dir():
        return
    for path in sorted(raw_root.glob("page_*.md")):
        try:
            page_number = int(path.stem.split("_")[-1])
        except ValueError:
            continue
        db.upsert_page(
            source_file_id, page_number, path.read_text(encoding="utf-8")
        )


def _resolve_page_markdown(
    db: WorkbenchDatabase,
    source_file_id: str,
    page_number: int,
    request: ResplitRequest,
) -> str:
    if request.markdown is not None:
        return request.markdown
    try:
        page = db.get_page(source_file_id, page_number)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="找不到该页") from error
    return page["edited_markdown"]


def _question_or_404(db: WorkbenchDatabase, question_id: str) -> dict[str, Any]:
    _safe_id(question_id, "q")
    try:
        return db.get_question(question_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="找不到题目") from error


def _validate_question(question: dict[str, Any], markdown: str | None = None):
    return payload_from_markdown(
        markdown if markdown is not None else question["edited_markdown"],
        question_id=question["question_id"],
        source_file_id=question["source_file_id"],
        ocr_markdown=question["ocr_markdown"],
        source_bbox=question["source_bbox"],
    )
