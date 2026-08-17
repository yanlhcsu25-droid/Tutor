"""OCR 服务层 — 上传、识别、审核、保存。

流程：
1. 上传图片/PDF → 保存到 uploads/ → 创建 OcrTask
   - PDF: pypdfium2 逐页转 PNG，每页独立 OCR
2. 通过子进程调用 PaddleOCR（避免内存崩溃）
3. 前端展示原图 + OCR 结果对比 → 教师逐块审核/订正
4. 保存 → 合并为 QuestionDraft
"""

import asyncio
import hashlib
import io
import json
import uuid
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import OcrBlock, OcrTask, QuestionDraft, Question, new_id
from calculus_agent.ocr.doc_pipeline import parse_pdf_to_candidates, _TYPE_MAP
from calculus_agent.ocr.pdf_preprocess import prepare_pdf_for_ocr, format_prepare_warning

UPLOADS_DIR = Path("uploads")
_WORKER_SCRIPT = Path(__file__).resolve().parent / "ocr_worker.py"


def _ensure_uploads() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def _save_upload(content: bytes, filename: str) -> Path:
    uploads = _ensure_uploads()
    ext = Path(filename).suffix.lower()
    safe_name = f"{uuid.uuid4().hex}{ext}"
    target = uploads / safe_name
    target.write_bytes(content)
    return target


def _is_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def _pdf_to_page_images(content: bytes) -> list[bytes]:
    """用 pypdfium2 将 PDF 逐页转为 PNG 图片字节。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    pages: list[bytes] = []
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=2.0)  # 2x 提高清晰度
        pil_image = bitmap.to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        pages.append(buf.getvalue())
        page.close()
    pdf.close()
    return pages


async def _run_ocr_async(image_path: str) -> dict:
    """异步子进程调用 PaddleOCR worker。"""
    python = str(
        Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            python, str(_WORKER_SCRIPT), image_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **__import__("os").environ,
                "PYTHONUNBUFFERED": "1",
                "OMP_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2",
            },
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace")[:500] if stderr else "unknown"
            return {
                "status": "failed",
                "engine": "paddleocr",
                "warnings": [f"OCR worker exit code {proc.returncode}: {err_msg}"],
                "blocks": [],
            }
        return json.loads(stdout.decode("utf-8"))
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "status": "failed",
            "engine": "paddleocr",
            "warnings": ["OCR 识别超时（300s）"],
            "blocks": [],
        }
    except json.JSONDecodeError:
        return {
            "status": "failed",
            "engine": "paddleocr",
            "warnings": ["OCR worker 返回数据格式错误"],
            "blocks": [],
        }


def create_ocr_task(session: Session, content: bytes, filename: str) -> OcrTask:
    """上传图片并创建 OCR 任务（立即返回，OCR 由后台 worker 异步处理）。"""
    image_path = _save_upload(content, filename)

    with Image.open(image_path) as img:
        width, height = img.size

    task = OcrTask(
        original_filename=filename,
        image_path=str(image_path),
        engine="paddleocr",
        status="pending",
        image_width=width,
        image_height=height,
    )
    session.add(task)
    return task


def process_pending_task(session: Session, task_id: str) -> bool:
    """对 pending 状态的 OCR 任务执行识别，更新结果并创建识别块。

    应在后台线程/worker 中调用此函数。"""
    task = session.get(OcrTask, task_id)
    if task is None or task.status != "pending":
        return False

    result = asyncio.run(_run_ocr_async(task.image_path))

    task.status = result["status"]
    task.engine = result.get("engine", "paddleocr")
    task.engine_version = result.get("engine_version")
    task.duration_ms = result.get("duration_ms")
    task.warnings_json = result.get("warnings", [])

    for block_data in result.get("blocks", []):
        bbox = block_data["bbox"]
        session.add(OcrBlock(
            task_id=task.id,
            block_order=block_data["block_order"],
            page_number=block_data.get("page_number", 1),
            block_type=block_data["block_type"],
            bbox_x=bbox[0],
            bbox_y=bbox[1],
            bbox_w=bbox[2],
            bbox_h=bbox[3],
            original_text=block_data["original_text"],
            original_latex=block_data.get("original_latex"),
            confidence=block_data["confidence"],
            review_status="pending",
        ))

    return True


async def create_ocr_task_async(session: Session, content: bytes, filename: str) -> OcrTask:
    """上传图片/PDF、创建任务并执行 OCR（一站式接口）。

    - 图片：直接 OCR
    - PDF：PaddleOCR 原生支持，直接传 PDF 文件；pypdfium2 仅生成预览图
    """
    is_pdf = _is_pdf(content)

    if is_pdf:
        # 保存 PDF 原文件
        pdf_path = _save_upload(content, filename)
        prepared = prepare_pdf_for_ocr(pdf_path)
        prepared.path.with_suffix(".preprocess.json").write_text(
            json.dumps(prepared.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 生成预览图（供前端展示）
        page_paths: list[str] = []
        first_width = first_height = 0
        for idx, page_bytes in enumerate(_pdf_to_page_images(content)):
            page_path = _save_upload(page_bytes, f"page_{idx + 1}.png")
            page_paths.append(str(page_path))
            if idx == 0:
                with Image.open(page_path) as img:
                    first_width, first_height = img.size

        task = OcrTask(
            original_filename=filename,
            image_path=page_paths[0] if page_paths else str(pdf_path),
            page_images_json=page_paths,
            engine="paddleocr",
            status="pending",
            image_width=first_width or None,
            image_height=first_height or None,
        )
        session.add(task)
        session.flush()

        # 直接把 PDF 丢给 PaddleOCR（原生支持多页）
        result = await _run_ocr_async(str(prepared.path))
        result.setdefault("warnings", []).append(format_prepare_warning(prepared.metadata))
    else:
        # 图片：原有逻辑
        task = create_ocr_task(session, content, filename)
        session.flush()
        result = await _run_ocr_async(task.image_path)

    task.status = result["status"]
    task.engine = result.get("engine", "paddleocr")
    task.engine_version = result.get("engine_version")
    task.duration_ms = result.get("duration_ms")
    task.warnings_json = result.get("warnings", [])

    for block_data in result.get("blocks", []):
        bbox = block_data["bbox"]
        session.add(OcrBlock(
            task_id=task.id,
            block_order=block_data["block_order"],
            page_number=block_data.get("page_number", 1),
            block_type=block_data["block_type"],
            bbox_x=bbox[0],
            bbox_y=bbox[1],
            bbox_w=bbox[2],
            bbox_h=bbox[3],
            original_text=block_data["original_text"],
            original_latex=block_data.get("original_latex"),
            confidence=block_data["confidence"],
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


# --- 文档级 OCR（PPStructureV3 + 题目切分）---

async def create_doc_ocr_task_async(
    session: Session,
    content: bytes,
    filename: str,
    *,
    subject: str = "高等数学",
) -> dict:
    """对教辅 PDF 运行 PPStructureV3，再按题切分入库。

    返回：
        {
            "success_count": int,
            "page_count": int,
            "drafts": [{"draft_id": ..., "question_number": ..., "page": ...}, ...],
        }

    现有的 /ocr/upload 用于单题图片 OCR + 逐块审核；
    本函数用于批量导入教辅 PDF → 自动拆题入库。
    """
    pdf_path = _save_upload(content, filename)
    prepared = prepare_pdf_for_ocr(pdf_path)
    pdf_path.with_suffix(".preprocess.json").write_text(
        json.dumps(prepared.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 在线程池中执行 PPStructureV3（避免阻塞事件循环）
    import concurrent.futures
    loop = asyncio.get_running_loop()
    candidates = await loop.run_in_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=1),
        lambda: parse_pdf_to_candidates(str(pdf_path), prepared_pdf=str(prepared.path)),
    )

    drafts: list[dict] = []
    for candidate in candidates:
        fingerprint = hashlib.sha256(
            candidate.body.encode("utf-8")
        ).hexdigest()

        # 未识别题型落 unknown（待人工），不静默当作计算题。
        question_type_cn = _TYPE_MAP.get(candidate.question_type, "unknown")

        # 构建选项 JSON（适配 QuestionDraft.options_json 格式）
        options_json = [
            {"key": k, "value": v} for k, v in candidate.options.items()
        ] if candidate.options else []

        # 构建参考答案 JSON
        reference_answers = [candidate.answer] if candidate.answer else []

        draft = QuestionDraft(
            source_name="ocr_doc",
            source_item_id=f"{candidate.original_number}_p{candidate.page_number}",
            variant=1,
            subject=subject,
            language="zh-CN",
            question_type=question_type_cn,
            question_text=candidate.body,
            reference_answers_json=reference_answers,
            options_json=options_json,
            solution_text=candidate.analysis or None,
            image_path=str(pdf_path),
            source_topic=f"第{candidate.page_number}页",
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
            "page": candidate.page_number,
            "question_type": question_type_cn,
        })

    return {
        "success_count": len(drafts),
        "page_count": max(
            (c.page_number for c in candidates), default=0
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
