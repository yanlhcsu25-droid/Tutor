from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import shutil
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pypdfium2 as pdfium
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.db import build_session_factory

from .database import PublishedDraftError, PublishedSourceError, WorkbenchDatabase
from calculus_agent.knowledge.retrieval import retrieve_knowledge
from .markdown_schema import payload_from_markdown, render_preview
from .ocr import (
    persist_rendered_draft,
    render_drafts,
    run_ocr_into_database,
    trace_split_pages,
)
from .import_pipeline import DocumentLayout, import_document
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


class SubmitRequest(BaseModel):
    question_ids: list[str] | None = None


class PublishRequest(BaseModel):
    question_ids: list[str] = Field(min_length=1)


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

@app.get("/api/sources")
def list_sources() -> dict[str, Any]:
    with _get_session() as session:
        return {"items": _get_db(session).list_sources()}


@app.post("/api/sources")
async def upload_source(
    file: UploadFile = File(...),
    solution_mode: str = Form("inline"),
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
    if solution_mode == "separate":
        values = (question_page_start, question_page_end, solution_page_start, solution_page_end)
        if any(value is None or value < 1 for value in values):
            raise HTTPException(status_code=400, detail="套卷模式必须填写有效的题目页和答案页范围")
        assert question_page_start is not None and question_page_end is not None
        assert solution_page_start is not None and solution_page_end is not None
        if question_page_start > question_page_end or solution_page_start > solution_page_end:
            raise HTTPException(status_code=400, detail="页码范围起始页不能大于结束页")
        layout = DocumentLayout(
            solution_mode="separate",
            question_pages=list(range(question_page_start, question_page_end + 1)),
            solution_pages=list(range(solution_page_start, solution_page_end + 1)),
        )
    else:
        layout = DocumentLayout(solution_mode="inline")
    source_file_id = f"src_{uuid.uuid4().hex}"
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

    with _get_session() as session:
        db = _get_db(session)
        initial_layout = layout.to_dict()
        initial_layout["progress"] = {
            "current_page": 0, "total_pages": actual_page_count, "status": "queued"
        }
        source = db.create_source(
            source_file_id, filename, stored_path, digest.hexdigest(), initial_layout
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

    diagnostics: list[Any] = []

    def run_sync() -> tuple[int, int]:
        with _get_session() as session:
            return run_ocr_into_database(
                stored_path,
                source_file_id,
                _get_db(session),
                raw_root=DATA_ROOT / "ocr_raw" / source_file_id,
                layout=layout,
                diagnostics_out=diagnostics,
            )

    try:
        page_count, question_count = await run_in_threadpool(run_sync)
    except Exception as error:
        with _get_session() as session:
            db = _get_db(session)
            db.finish_source(source_file_id, page_count=0, error=str(error))
            db.update_processing(source_file_id, status="failed", error=str(error))
        raise HTTPException(status_code=422, detail=f"OCR或题目切分失败：{error}") from error

    with _get_session() as session:
        db = _get_db(session)
        if layout.solution_mode == "inline":
            layout = DocumentLayout("inline", list(range(1, page_count + 1)), [])
        saved_layout = layout.to_dict()
        saved_layout["workflow_stage"] = "markdown_reviewing"
        db.save_source_layout(source_file_id, saved_layout)
        db.finish_source(source_file_id, page_count=page_count)
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
    """Atomically delete an import with no formal Question, then clean source files."""
    _safe_id(source_file_id, "src")
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
def list_questions(source_file_id: str) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        _source_or_404(db, source_file_id)
        return {"items": db.list_questions(source_file_id)}


@app.get("/api/questions/{question_id}")
def get_question(question_id: str) -> dict[str, Any]:
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        source = _source_or_404(db, question["source_file_id"])
        return {"question": question, "source": source}


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
    published_keys = {(item["page_number"], item["original_number"]) for item in published}
    conflict_items = [item for item in published
                      if (item["page_number"], item["original_number"]) in
                      {(draft.page_number, draft.original_number) for draft in rendered}]
    conflicts = [item["original_number"] for item in conflict_items]
    return {
        "source_file_id": source_file_id,
        "layout": layout.to_dict(),
        "new_numbers": [item.original_number for item in rendered],
        "created": [{"page_number": item.page_number, "original_number": item.original_number,
                     "match_status": item.match_status, "review_note": item.review_note,
                     "preview": " ".join(item.markdown.split())[:160]} for item in rendered],
        "old_unpublished": [item for item in old if item["review_status"] != "published"],
        "published_conflicts": conflict_items,
        "diagnostics": {
            "ambiguous_keys": result.diagnostics.ambiguous_keys,
            "missing_questions": [item.key[1] for item in result.diagnostics.missing_questions],
            "unmatched_solutions": [item.key[1] for item in result.diagnostics.unmatched_solutions],
        },
        "blocked": bool(conflicts),
        "_rendered": rendered,
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
        from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook

        nodes = session.scalars(
            select(KnowledgeNode)
            .join(CurriculumNode, KnowledgeNode.curriculum_node_id == CurriculumNode.id)
            .join(Textbook, CurriculumNode.textbook_id == Textbook.id)
            .where(
                Textbook.is_active.is_(True),
                CurriculumNode.node_type == "section",
                KnowledgeNode.review_status == "approved",
            )
        ).all()
        options = [{"knowledge_id": node.id, "name": node.name} for node in nodes]
        return {"question_id": question_id, "options": options}


@app.post("/api/questions/{question_id}/knowledge/classify")
def classify_workbench_question(question_id: str) -> dict[str, Any]:
    """审核内容确认后调用的只读目录分类工具。"""
    with _get_session() as session:
        db = _get_db(session)
        question = _question_or_404(db, question_id)
        if not question.get("content_confirmed"):
            raise HTTPException(status_code=409, detail="请先确认题目内容")
        text = f"{question['edited_markdown']}"
        from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook
        nodes = session.scalars(
            select(KnowledgeNode).join(CurriculumNode, KnowledgeNode.curriculum_node_id == CurriculumNode.id)
            .join(Textbook, CurriculumNode.textbook_id == Textbook.id)
            .where(Textbook.is_active.is_(True), CurriculumNode.node_type == "section", KnowledgeNode.review_status == "approved")
        ).all()
        legal_ids = {node.id for node in nodes}
        ranked = [
            {"knowledge_id": item.node.id, "name": item.node.name, "confidence": item.score}
            for item in retrieve_knowledge(session, text, limit=10)
            if item.node.id in legal_ids
        ][:3]
        options = [{"knowledge_id": node.id, "name": node.name} for node in nodes]
        return {
            "question_id": question_id,
            "knowledge_points": ranked[:3],
            "options": options,
            "needs_review": not (1 <= len(ranked[:3]) <= 3),
            "reason": "基于当前激活教材目录和本地术语匹配规则推荐",
            "provenance": "rule_suggested",
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
