from __future__ import annotations

import uuid
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    OcrImportDraft,
    OcrImportSource,
    OcrPage,
    Question,
    QuestionDraft,
)


class PublishedDraftError(Exception):
    """已发布草稿不允许通过普通审核保存修改（发布封板）。

    状态机要求单向：pending → in_review → reviewed → published，
    普通保存不得把 published 回退为 in_review。
    """

    def __init__(self, message: str = "已发布题目不能直接修改，请先创建修订版本或执行重新审核流程。") -> None:
        super().__init__(message)


class PublishedSourceError(Exception):
    """A source with formal Question rows cannot be deleted."""


class WorkbenchDatabase:
    """OcrImportSource / OcrImportDraft 的只读 CRUD 层。

    不包含任何业务逻辑：Markdown 解析、字段映射、校验、发布等
    请使用 QuestionImportService。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ── Source CRUD ──

    def create_source(
        self,
        source_file_id: str,
        original_name: str,
        stored_path: str,
        sha256: str,
        layout: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self._session.scalar(
            select(OcrImportSource).where(OcrImportSource.sha256 == sha256)
        )
        if existing is not None:
            return _source_dict(existing)

        source = OcrImportSource(
            id=source_file_id,
            original_name=original_name,
            stored_path=str(stored_path),
            sha256=sha256,
            processing_status="queued",
            layout_json=layout,
        )
        self._session.add(source)
        self._session.flush()
        return _source_dict(source)

    def save_source_layout(self, source_file_id: str, layout: dict[str, Any]) -> None:
        source = self._session.get(OcrImportSource, source_file_id)
        if source is None:
            raise KeyError(source_file_id)
        source.layout_json = layout
        self._session.flush()

    def update_processing(self, source_file_id: str, **values: Any) -> None:
        source = self._session.get(OcrImportSource, source_file_id)
        if source is None:
            raise KeyError(source_file_id)
        layout = dict(source.layout_json or {})
        progress = dict(layout.get("progress") or {})
        progress.update(values)
        layout["progress"] = progress
        stage = values.get("status")
        if stage == "queued":
            source.processing_status = "queued"
        elif stage in {"ocr", "matching"}:
            source.processing_status = "processing"
        elif stage == "completed":
            source.processing_status = "completed"
        elif stage in {"failed", "cancelled"}:
            source.processing_status = "failed"
        source.layout_json = layout
        self._session.flush()

    def finish_source(
        self, source_file_id: str, *, page_count: int, error: str | None = None
    ) -> None:
        source = self._session.get(OcrImportSource, source_file_id)
        if source is None:
            raise KeyError(source_file_id)
        source.page_count = page_count
        source.processing_status = "failed" if error else "completed"
        source.processing_error = error

    def get_source(self, source_file_id: str) -> dict[str, Any]:
        source = self._session.get(OcrImportSource, source_file_id)
        if source is None:
            raise KeyError(source_file_id)
        return self._source_summary(source)

    def list_sources(self) -> list[dict[str, Any]]:
        sources = self._session.scalars(
            select(OcrImportSource).order_by(OcrImportSource.created_at.desc())
        ).all()
        return [self._source_summary(source) for source in sources]

    def _source_summary(self, source: OcrImportSource) -> dict[str, Any]:
        drafts = list(self._session.scalars(
            select(OcrImportDraft).where(OcrImportDraft.source_id == source.id)
        ).all())
        draft_ids = [item.id for item in drafts]
        published_count = 0
        if draft_ids:
            published_count = int(self._session.scalar(
                select(func.count(Question.id))
                .join(QuestionDraft, QuestionDraft.id == Question.draft_id)
                .where(
                    QuestionDraft.source_name == "ocr_import",
                    QuestionDraft.source_item_id.in_(draft_ids),
                )
            ) or 0)
        total = len(drafts)
        completed = sum(item.review_status in {"reviewed", "published"} for item in drafts)
        if total and completed == total:
            review_status = "completed"
        elif completed:
            review_status = "in_progress"
        else:
            review_status = "pending"
        page_manual = int(self._session.scalar(
            select(func.count(OcrPage.id)).where(
                OcrPage.source_id == source.id,
                OcrPage.edited_markdown != OcrPage.raw_markdown,
            )
        ) or 0)
        draft_manual = sum(item.edited_markdown != item.ocr_markdown for item in drafts)
        result = _source_dict(source)
        progress = (source.layout_json or {}).get("progress") or {}
        result["progress"] = progress
        result.update({
            "question_count": total,
            "reviewed_count": completed,
            "review": {"status": review_status, "completed": completed, "total": total},
            "published_count": published_count,
            "can_delete": published_count == 0,
            "has_manual_edits": bool(page_manual or draft_manual),
            "manual_edit_count": page_manual + draft_manual,
        })
        return result

    def delete_unpublished_source(self, source_file_id: str) -> dict[str, Any]:
        """Delete one source's DB lifecycle atomically; caller cleans files after commit."""
        source = self._session.get(OcrImportSource, source_file_id)
        if source is None:
            raise KeyError(source_file_id)
        drafts = list(self._session.scalars(
            select(OcrImportDraft).where(OcrImportDraft.source_id == source_file_id)
        ).all())
        pages = list(self._session.scalars(
            select(OcrPage).where(OcrPage.source_id == source_file_id)
        ).all())
        draft_ids = [item.id for item in drafts]
        bank_drafts: list[QuestionDraft] = []
        formal_count = 0
        if draft_ids:
            bank_drafts = list(self._session.scalars(
                select(QuestionDraft).where(
                    QuestionDraft.source_name == "ocr_import",
                    QuestionDraft.source_item_id.in_(draft_ids),
                )
            ).all())
            bank_ids = [item.id for item in bank_drafts]
            if bank_ids:
                formal_count = int(self._session.scalar(
                    select(func.count(Question.id)).where(Question.draft_id.in_(bank_ids))
                ) or 0)
        if formal_count:
            raise PublishedSourceError("已有题目发布到正式题库，不能删除该 PDF 导入记录。")

        manual_count = sum(item.edited_markdown != item.raw_markdown for item in pages)
        manual_count += sum(item.edited_markdown != item.ocr_markdown for item in drafts)
        result = {
            "source_id": source.id,
            "stored_path": source.stored_path,
            "deleted_page_count": len(pages),
            "deleted_draft_count": len(drafts),
            "deleted_bank_draft_count": len(bank_drafts),
            "had_manual_edits": bool(manual_count),
            "manual_edit_count": manual_count,
        }
        for item in bank_drafts:
            self._session.delete(item)
        for item in drafts:
            self._session.delete(item)
        for item in pages:
            self._session.delete(item)
        self._session.delete(source)
        self._session.flush()
        return result

    # ── Draft CRUD ──

    def add_question(
        self,
        *,
        question_id: str,
        source_file_id: str,
        page_number: int,
        original_number: str,
        bbox: dict[str, Any] | None,
        ocr_markdown: str,
        match_status: str = "matched",
        match_method: str = "inline",
        review_note: str = "",
    ) -> None:
        draft = OcrImportDraft(
            id=question_id,
            source_id=source_file_id,
            page_number=page_number,
            original_number=original_number,
            bbox_json=bbox,
            ocr_markdown=ocr_markdown,
            edited_markdown=ocr_markdown,
            review_status="pending",
            match_status=match_status,
            match_method=match_method,
            review_note=review_note,
        )
        self._session.add(draft)
        self._session.flush()

    def list_questions(self, source_file_id: str) -> list[dict[str, Any]]:
        drafts = self._session.scalars(
            select(OcrImportDraft)
            .where(OcrImportDraft.source_id == source_file_id)
            .order_by(OcrImportDraft.page_number, OcrImportDraft.created_at)
        ).all()
        return [_draft_dict(draft) for draft in drafts]

    def get_question(self, question_id: str) -> dict[str, Any]:
        draft = self._session.get(OcrImportDraft, question_id)
        if draft is None:
            raise KeyError(question_id)
        return _draft_dict(draft)

    def save_question(
        self,
        question_id: str,
        markdown: str,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        draft = self._session.get(OcrImportDraft, question_id)
        if draft is None:
            raise KeyError(question_id)
        if draft.review_status == "published":
            raise PublishedDraftError(
                "已发布题目不能直接修改，请先创建修订版本或执行重新审核流程。"
            )
        content_changed = _content_signature(draft.edited_markdown) != _content_signature(markdown)
        draft.edited_markdown = markdown
        if content_changed:
            draft.content_confirmed = False
        # 只有人工实际补充了参考解答，才解除答案匹配门禁。
        # 单独修改题干不能把 ambiguous/missing_answer 静默恢复为 matched。
        solution = _markdown_section(markdown, ("参考解答", "答案", "解析"))
        if solution.strip() and draft.match_status in {"ambiguous", "missing_answer"}:
            draft.match_status = "matched"
        # 结构化元数据（knowledge_points_json / difficulty_level）以数据库字段为事实来源，
        # 不再从被反复人工修改的 Markdown 反解，避免 UUID / literal \n 等污染写回字段。
        draft.review_status = "in_review"
        draft.validation_json = validation
        self._session.flush()
        return _draft_dict(draft)

    def update_metadata(
        self,
        question_id: str,
        *,
        knowledge_points: list[str],
        difficulty_level: int | None,
        content_confirmed: bool | None = None,
    ) -> dict[str, Any]:
        """仅更新结构化元数据字段，绝不触碰 ocr_markdown / edited_markdown。

        knowledge_points 必须是 knowledge_id（UUID）列表，最多 3 个，原样存储。
        名称只是展示字段，由前端按 id 反查，绝不在后端/Markdown 中保存。
        该方法是保存知识点/难度的唯一入口，保证 Markdown 不被元数据污染。
        """
        draft = self._session.get(OcrImportDraft, question_id)
        if draft is None:
            raise KeyError(question_id)
        if draft.review_status == "published":
            raise PublishedDraftError("已发布题目不能修改元数据，请先创建修订版本")
        ids = list(dict.fromkeys(p.strip() for p in knowledge_points if p and p.strip()))[:3]
        draft.knowledge_points_json = ids
        draft.difficulty_level = difficulty_level
        if content_confirmed is not None:
            draft.content_confirmed = content_confirmed
        self._session.flush()
        return _draft_dict(draft)

    def confirm_content(self, question_id: str) -> dict[str, Any]:
        draft = self._session.get(OcrImportDraft, question_id)
        if draft is None:
            raise KeyError(question_id)
        if draft.review_status == "published":
            raise PublishedDraftError()
        draft.content_confirmed = True
        self._session.flush()
        return _draft_dict(draft)

    def mark_reviewed(
        self, question_id: str, validation: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """将校验通过的草稿推进到 reviewed，不修改题目正文。"""
        draft = self._session.get(OcrImportDraft, question_id)
        if draft is None:
            raise KeyError(question_id)
        if draft.review_status != "published":
            draft.review_status = "reviewed"
            draft.validation_json = validation
        self._session.flush()
        return _draft_dict(draft)

    def create_revision(self, question_id: str) -> dict[str, Any]:
        """基于已发布题目创建新的审核草稿，保留原发布版本。"""
        source = self._session.get(OcrImportDraft, question_id)
        if source is None:
            raise KeyError(question_id)
        if source.review_status != "published":
            raise ValueError("只有已发布题目可以创建修订版本")
        revision = OcrImportDraft(
            id=f"q_{uuid.uuid4().hex}",
            source_id=source.source_id,
            page_number=source.page_number,
            original_number=source.original_number,
            ocr_markdown=source.ocr_markdown,
            edited_markdown=source.edited_markdown,
            review_status="in_review",
            bbox_json=source.bbox_json,
            validation_json=None,
            revision_of_id=source.id,
            formal_question_id=source.formal_question_id,
            knowledge_points_json=list(source.knowledge_points_json or []),
            difficulty_level=source.difficulty_level,
        )
        self._session.add(revision)
        self._session.flush()
        return _draft_dict(revision)

    def list_questions_by_pages(
        self, source_file_id: str, page_numbers: Sequence[int]
    ) -> list[dict[str, Any]]:
        """按来源页码批量取草稿。用于重新切题时确定受影响的旧草稿。"""
        if not page_numbers:
            return []
        drafts = self._session.scalars(
            select(OcrImportDraft)
            .where(
                OcrImportDraft.source_id == source_file_id,
                OcrImportDraft.page_number.in_(list(page_numbers)),
            )
            .order_by(OcrImportDraft.page_number, OcrImportDraft.created_at)
        ).all()
        return [_draft_dict(draft) for draft in drafts]

    def delete_questions(self, question_ids: Sequence[str]) -> int:
        """按 id 删除草稿。调用方负责校验 published 保护。"""
        deleted = 0
        for question_id in question_ids:
            draft = self._session.get(OcrImportDraft, question_id)
            if draft is None:
                continue
            self._session.delete(draft)
            deleted += 1
        self._session.flush()
        return deleted

    # ── Page CRUD（整页 Markdown，切题输入快照）──

    def upsert_page(
        self,
        source_file_id: str,
        page_number: int,
        raw_markdown: str,
        *,
        reset_edited: bool = False,
    ) -> dict[str, Any]:
        """写入整页原文。已存在时只刷新 raw_markdown，人工修改默认不被覆盖。"""
        page = self._session.scalar(
            select(OcrPage).where(
                OcrPage.source_id == source_file_id,
                OcrPage.page_number == page_number,
            )
        )
        now = datetime.now(UTC)
        if page is None:
            page = OcrPage(
                id=f"pg_{uuid.uuid4().hex}",
                source_id=source_file_id,
                page_number=page_number,
                raw_markdown=raw_markdown,
                edited_markdown=raw_markdown,
                created_at=now,
                updated_at=now,
            )
            self._session.add(page)
        else:
            page.raw_markdown = raw_markdown
            if reset_edited:
                page.edited_markdown = raw_markdown
            page.updated_at = now
        self._session.flush()
        return _page_dict(page)

    def save_page_markdown(
        self, source_file_id: str, page_number: int, markdown: str
    ) -> dict[str, Any]:
        page = self._session.scalar(
            select(OcrPage).where(
                OcrPage.source_id == source_file_id,
                OcrPage.page_number == page_number,
            )
        )
        if page is None:
            raise KeyError(f"{source_file_id}#{page_number}")
        page.edited_markdown = markdown
        page.updated_at = datetime.now(UTC)
        self._session.flush()
        return _page_dict(page)

    def restore_page_markdown(self, source_file_id: str, page_number: int) -> dict[str, Any]:
        """恢复 OCR 原文到编辑副本；不重新运行 OCR。"""
        page = self._session.scalar(
            select(OcrPage).where(
                OcrPage.source_id == source_file_id,
                OcrPage.page_number == page_number,
            )
        )
        if page is None:
            raise KeyError(page_number)
        page.edited_markdown = page.raw_markdown
        self._session.flush()
        return _page_dict(page)

    def get_page(self, source_file_id: str, page_number: int) -> dict[str, Any]:
        page = self._session.scalar(
            select(OcrPage).where(
                OcrPage.source_id == source_file_id,
                OcrPage.page_number == page_number,
            )
        )
        if page is None:
            raise KeyError(f"{source_file_id}#{page_number}")
        return _page_dict(page)

    def list_pages(self, source_file_id: str) -> list[dict[str, Any]]:
        pages = self._session.scalars(
            select(OcrPage)
            .where(OcrPage.source_id == source_file_id)
            .order_by(OcrPage.page_number)
        ).all()
        return [_page_dict(page) for page in pages]


# ── dict 序列化 ──

def _page_dict(page: OcrPage) -> dict[str, Any]:
    return {
        "source_file_id": page.source_id,
        "page_number": page.page_number,
        "raw_markdown": page.raw_markdown,
        "edited_markdown": page.edited_markdown,
        "modified": page.edited_markdown != page.raw_markdown,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }

def _source_dict(source: OcrImportSource) -> dict[str, Any]:
    return {
        "source_file_id": source.id,
        "original_name": source.original_name,
        "stored_path": source.stored_path,
        "sha256": source.sha256,
        "page_count": source.page_count,
        "processing_status": source.processing_status,
        "processing_error": source.processing_error,
        "layout": source.layout_json,
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }


def _draft_dict(draft: OcrImportDraft) -> dict[str, Any]:
    return {
        "question_id": draft.id,
        "source_file_id": draft.source_id,
        "page_number": draft.page_number,
        "original_number": draft.original_number,
        "ocr_markdown": draft.ocr_markdown,
        "edited_markdown": draft.edited_markdown,
        "review_status": draft.review_status,
        "match_status": draft.match_status,
        "match_method": draft.match_method,
        "review_note": draft.review_note,
        "source_bbox": draft.bbox_json,
        "validation": draft.validation_json,
        "knowledge_points": list(draft.knowledge_points_json or []),
        "difficulty_level": draft.difficulty_level,
        "formal_question_id": draft.formal_question_id,
        "revision_of_id": draft.revision_of_id,
        "content_confirmed": draft.content_confirmed,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def _markdown_section(markdown: str, names: tuple[str, ...]) -> str:
    import re
    pattern = r"(?ms)^##\s+(?:" + "|".join(names) + r")\s*\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, markdown)
    return match.group(1).strip() if match else ""


def _parse_difficulty(markdown: str) -> int | None:
    value = _markdown_section(markdown, ("难度", "difficulty_level", "difficulty"))
    try:
        level = int(value.strip())
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 5 else None


def _parse_knowledge_points(markdown: str) -> list[str]:
    value = _markdown_section(markdown, ("知识点", "knowledge_points"))
    return list(dict.fromkeys(item.strip() for item in re.split(r"[,，、\n]", value) if item.strip()))[:3]


def _content_signature(markdown: str) -> tuple[str, str, str]:
    return tuple(_markdown_section(markdown, (name,)) for name in ("题目内容", "参考解答", "题型"))
