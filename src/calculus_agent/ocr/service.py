"""Unified MinerU OCR service for uploads, review, and draft persistence."""

import asyncio
import hashlib
import io
import tempfile
import uuid
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import OcrBlock, OcrTask, QuestionDraft, Question
from calculus_agent.ocr.mineru_adapter import content_blocks_to_pages, run_mineru

UPLOADS_DIR = Path("uploads")


def _ensure_uploads() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def _save_upload(content: bytes, filename: str) -> Path:
    target = _ensure_uploads() / f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
    target.write_bytes(content)
    return target


def _is_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def _pdf_to_page_images(content: bytes) -> list[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    pages: list[bytes] = []
    try:
        for page in pdf:
            bitmap = page.render(scale=2.0)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pages.append(buffer.getvalue())
            page.close()
    finally:
        pdf.close()
    return pages


def _image_as_pdf(source: Path, target: Path) -> Path:
    with Image.open(source) as image:
        image.convert("RGB").save(target, format="PDF")
    return target


def _run_mineru_pages(pdf_path: Path) -> tuple[list[tuple[int, str]], dict]:
    with tempfile.TemporaryDirectory(prefix="mineru-upload-") as output:
        blocks, metrics = run_mineru(pdf_path, Path(output))
    page_count = max(
        (int(block.get("page_idx", -1)) for block in blocks), default=-1
    ) + 1
    return content_blocks_to_pages(blocks, tuple(range(1, page_count + 1))), metrics


async def create_ocr_task_async(session: Session, content: bytes, filename: str) -> OcrTask:
    """Run every image/PDF upload through MinerU and persist page Markdown blocks."""
    source_path = _save_upload(content, filename)
    is_pdf = _is_pdf(content)
    pdf_path = source_path if is_pdf else _image_as_pdf(
        source_path, source_path.with_suffix(".pdf")
    )
    preview_bytes = _pdf_to_page_images(content) if is_pdf else [content]
    preview_paths = [
        str(_save_upload(page, f"page_{index}.png"))
        for index, page in enumerate(preview_bytes, start=1)
    ]
    width = height = None
    if preview_paths:
        with Image.open(preview_paths[0]) as image:
            width, height = image.size

    task = OcrTask(
        original_filename=filename,
        image_path=preview_paths[0] if preview_paths else str(source_path),
        page_images_json=preview_paths,
        engine="mineru",
        status="pending",
        image_width=width,
        image_height=height,
    )
    session.add(task)
    session.flush()

    pages, metrics = await asyncio.to_thread(_run_mineru_pages, pdf_path)
    task.status = "completed"
    task.engine = "mineru"
    task.duration_ms = int(float(metrics.get("elapsed_seconds", 0)) * 1000)
    task.warnings_json = []
    for order, (page_number, markdown) in enumerate(pages):
        if not markdown.strip():
            continue
        session.add(OcrBlock(
            task_id=task.id,
            block_order=order,
            page_number=page_number,
            block_type="markdown",
            bbox_x=0,
            bbox_y=0,
            bbox_w=0,
            bbox_h=0,
            original_text=markdown,
            confidence=1.0,
            review_status="pending",
        ))
    return task


def get_ocr_task(session: Session, task_id: str) -> dict | None:
    """获取 OCR 任务详情（含所有识别块和原图路径）。"""
    task = session.get(OcrTask, task_id)
    if task is None:
        return None
    blocks = list(
        session.scalars(
            select(OcrBlock)
            .where(OcrBlock.task_id == task.id)
            .order_by(OcrBlock.block_order)
        ).all()
    )
    return {
        "task_id": task.id,
        "original_filename": task.original_filename,
        "image_path": task.image_path,
        "page_images": task.page_images_json or [],
        "engine": task.engine,
        "status": task.status,
        "image_width": task.image_width,
        "image_height": task.image_height,
        "duration_ms": task.duration_ms,
        "warnings": task.warnings_json,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "blocks": [_block_to_dict(b) for b in blocks],
    }


def update_ocr_block(
    session: Session, block_id: str, corrected_text: str | None = None,
    corrected_latex: str | None = None, review_status: str = "approved",
) -> dict | None:
    """教师审核/订正单个 OCR 块。"""
    block = session.get(OcrBlock, block_id)
    if block is None:
        return None
    if corrected_text is not None:
        block.corrected_text = corrected_text
    if corrected_latex is not None:
        block.corrected_latex = corrected_latex
    block.review_status = review_status
    return _block_to_dict(block)


def save_ocr_as_draft(
    session: Session, task_id: str, merged_text: str | None = None,
    question_type: str = "unknown", subject: str = "高等数学",
) -> dict | None:
    """将 OCR 审核结果合并保存为 QuestionDraft + Question。

    默认合并所有 block 的 corrected_text（回退到 original_text）。
    """
    task = session.get(OcrTask, task_id)
    if task is None:
        return None

    blocks = list(
        session.scalars(
            select(OcrBlock)
            .where(OcrBlock.task_id == task.id)
            .order_by(OcrBlock.block_order)
        ).all()
    )

    if merged_text is None:
        parts = []
        for block in blocks:
            text = block.corrected_text or block.original_text
            if text.strip():
                parts.append(text.strip())
        merged_text = "\n".join(parts)

    for block in blocks:
        block.merged_question_text = merged_text
        if block.review_status == "pending":
            block.review_status = "approved"

    fingerprint = hashlib.sha256(merged_text.encode("utf-8")).hexdigest()

    draft = QuestionDraft(
        source_name="ocr_upload",
        source_item_id=task_id,
        variant=1,
        subject=subject,
        language="zh-CN",
        question_type=question_type,
        question_text=merged_text,
        image_path=task.image_path,
        normalized_fingerprint=fingerprint,
        status="pending",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        question_text=merged_text,
        question_type=question_type,
        verification_status="manual_review",
        review_status="pending",
    )
    session.add(question)

    return {
        "draft_id": draft.id,
        "question_id": question.id,
        "question_text": merged_text,
        "question_type": question_type,
        "block_count": len(blocks),
    }


def _block_to_dict(block: OcrBlock) -> dict:
    return {
        "block_id": block.id,
        "block_order": block.block_order,
        "page_number": getattr(block, "page_number", 1) or 1,
        "block_type": block.block_type,
        "bbox": [block.bbox_x, block.bbox_y, block.bbox_w, block.bbox_h],
        "original_text": block.original_text,
        "original_latex": block.original_latex,
        "confidence": block.confidence,
        "corrected_text": block.corrected_text,
        "corrected_latex": block.corrected_latex,
        "review_status": block.review_status,
    }


# --- 文档级 OCR（MinerU + 题目切分）---

async def create_doc_ocr_task_async(
    session: Session,
    content: bytes,
    filename: str,
    *,
    subject: str = "高等数学",
) -> dict:
    """Run MinerU, then split its page Markdown into reviewable questions."""
    pdf_path = _save_upload(content, filename)
    pages, _metrics = await asyncio.to_thread(_run_mineru_pages, pdf_path)
    from calculus_agent.workbench.ocr import split_pages_into_candidates

    candidates = split_pages_into_candidates(pages)

    drafts: list[dict] = []
    for placed in candidates:
        candidate = placed.candidate
        page_number = placed.page_number
        fingerprint = hashlib.sha256(
            candidate.body.encode("utf-8")
        ).hexdigest()

        # 未识别题型落 unknown（待人工），不静默当作计算题。
        question_type_cn = _WORKBENCH_TYPE_MAP.get(
            candidate.question_type, "unknown"
        )

        # 构建选项 JSON（适配 QuestionDraft.options_json 格式）
        options_json = [
            {"key": k, "value": v} for k, v in candidate.options.items()
        ] if candidate.options else []

        # 构建参考答案 JSON
        reference_answers = [candidate.answer] if candidate.answer else []

        draft = QuestionDraft(
            source_name="ocr_doc",
            source_item_id=f"{candidate.original_number}_p{page_number}",
            variant=1,
            subject=subject,
            language="zh-CN",
            question_type=question_type_cn,
            question_text=candidate.body,
            reference_answers_json=reference_answers,
            options_json=options_json,
            solution_text=candidate.analysis or None,
            image_path=str(pdf_path),
            source_topic=f"第{page_number}页",
            normalized_fingerprint=fingerprint,
            status="pending",
        )
        session.add(draft)
        session.flush()

        question = Question(
            draft_id=draft.id,
            question_text=candidate.body,
            question_type=question_type_cn,
            final_answer=candidate.answer or None,
            verification_status="manual_review",
            review_status="pending",
        )
        session.add(question)

        drafts.append({
            "draft_id": draft.id,
            "question_id": question.id,
            "question_number": candidate.original_number,
            "page": page_number,
            "question_type": question_type_cn,
        })

    return {
        "success_count": len(drafts),
        "page_count": max(
            (item.page_number for item in candidates), default=0
        ),
        "drafts": drafts,
    }


# --- 工作台 → 主题库同步 ---

# 工作台内部词表 → 正式业务题型（五类 contract）。
# ``subjective`` / ``other`` 表示分类器未能判定为四个可组卷题型之一，
# 因此一律落成 ``unknown``（题型待定，需人工处理），绝不静默塞进计算题。
_WORKBENCH_TYPE_MAP: dict[str, str] = {
    "selection": "选择题",
    "single_choice": "选择题",
    "multiple_choice": "选择题",
    "fill_blank": "填空题",
    "calculation": "计算题",
    "proof": "证明题",
    "subjective": "unknown",
    "other": "unknown",
}


def sync_workbench_question_to_bank(
    session: Session,
    question: dict,
) -> dict | None:
    """将工作台发布的题目同步到组卷系统主题库（QuestionDraft + Question）。

    幂等：通过 source_name + source_item_id 唯一约束去重。
    返回 {"draft_id", "question_id"} 或 None（已存在时跳过）。
    """
    import hashlib as _hashlib

    structured = question.get("structured_json")
    if not structured:
        return None

    # structured_json 可能是 JSON 字符串或已解析的 dict
    if isinstance(structured, str):
        import json as _json
        structured = _json.loads(structured)

    stem = (structured.get("stem") or "").strip()
    options = structured.get("options") or {}
    answer = (structured.get("answer") or "").strip()
    analysis = (structured.get("analysis") or "").strip()
    wb_type = structured.get("question_type", "other")
    chapter = structured.get("chapter") or ""
    knowledge_points = structured.get("knowledge_points") or []
    difficulty = structured.get("difficulty")
    original_number = structured.get("original_number", "")
    source_file_id = structured.get("source_file_id", "")

    # 构建题干文本
    question_text = stem
    if options:
        option_lines = "\n".join(
            f"{k}. {v}" for k, v in sorted(options.items())
        )
        question_text = f"{stem}\n\n{option_lines}"

    # 映射题目类型
    # 未识别的工作台题型不得静默落成计算题，一律 unknown 交人工判定。
    main_question_type = _WORKBENCH_TYPE_MAP.get(wb_type, "unknown")

    # 映射难度等级
    level_map = {1: "easy", 2: "easy-medium", 3: "medium", 4: "medium-hard", 5: "hard"}
    level = level_map.get(difficulty) if isinstance(difficulty, int) else None

    # 构建选项 JSON
    options_json = [
        {"key": k, "text": v} for k, v in sorted(options.items())
    ]

    # 规范化指纹
    normalized_fingerprint = _hashlib.sha256(
        question_text.encode("utf-8")
    ).hexdigest()

    # 检查是否已存在
    existing = session.query(QuestionDraft).filter(
        QuestionDraft.source_name == source_file_id,
        QuestionDraft.source_item_id == original_number,
    ).first()
    if existing:
        return None

    draft = QuestionDraft(
        source_name=source_file_id,
        source_item_id=original_number,
        subject="高等数学",
        question_type=main_question_type,
        source_topic=chapter,
        question_text=question_text,
        options_json=options_json,
        reference_answers_json=[answer],
        solution_text=analysis,
        keywords_json=knowledge_points,
        level=level,
        status="approved",
        normalized_fingerprint=normalized_fingerprint,
        proposed_classification_json={
            "chapter": chapter,
            "knowledge_points": knowledge_points,
            "difficulty": difficulty,
        },
    )
    session.add(draft)
    session.flush()

    question_record = Question(
        draft_id=draft.id,
        question_text=question_text,
        question_type=main_question_type,
        final_answer=answer,
        solution_json={
            "analysis": analysis,
            "knowledge_points": knowledge_points,
        },
        verification_status="auto_imported",
        review_status="approved",
    )
    session.add(question_record)
    session.flush()

    return {
        "draft_id": draft.id,
        "question_id": question_record.id,
    }
