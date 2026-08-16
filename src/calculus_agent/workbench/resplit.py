"""基于人工修正后的整页 Markdown 重新切题。

背景：OCR 把题号识别坏时（`3.` → 异常字符、`4.` → `河4.`），现有 splitter
认不出新题号，整页会被当作 preamble 合并进上一页的 pending 题。用户人工把整页
Markdown 改回正确题号后，需要一条通道重新跑一次切题并**替换**旧草稿。

这里不重写任何切分或匹配规则：候选生成一律走
`import_pipeline.import_document`，模板渲染一律走 `ocr.render_drafts`，
与首次导入完全同一条代码路径。

跨页范围（affected range）
--------------------------
一道题的归属页 = 题号首次出现的页；它的正文可能延伸到后面若干页
（后续页的 preamble）。定义：

    页边界 k|k+1 是"干净"的  ⟺  第 k+1 页没有 preamble

干净边界处不存在跨页内容依赖，因此文档被干净边界切成若干互不影响的**段**。
只要以"包含目标页的整段"为单位重新切题，结果就和整篇重跑一致，
既不会漏掉被上一页吞掉的内容，也不会波及无关页面。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .database import WorkbenchDatabase
from .import_pipeline import DocumentLayout, ImportDiagnostics, import_document
from .ocr import (
    RenderedDraft,
    page_has_continuation,
    persist_rendered_draft,
    render_drafts,
)


PREVIEW_CHARS = 160


class ResplitError(Exception):
    """重新切题被拒绝。"""


class ResplitBlockedError(ResplitError):
    """兼容旧调用的重建阻塞异常；已发布草稿不再触发该异常。"""

    def __init__(self, drafts: list[dict[str, Any]]) -> None:
        numbers = "、".join(
            f"第{item['page_number']}页 第{item['original_number']}题" for item in drafts
        )
        super().__init__(
            f"受影响范围内存在已发布题目（{numbers}），无法重新切题。"
            "请先在正式题库中撤回或修订这些题目。"
        )
        self.drafts = drafts


class ResplitStaleError(ResplitError):
    """preview 与 apply 之间切题结果发生变化。"""

    def __init__(self, expected: list[str], actual: list[str]) -> None:
        super().__init__(
            f"切题结果已变化（预览为 {expected}，当前为 {actual}），"
            "请重新预览后再确认。"
        )
        self.expected = expected
        self.actual = actual


@dataclass
class ResplitPlan:
    source_file_id: str
    target_page: int
    start_page: int
    end_page: int
    old_drafts: list[dict[str, Any]] = field(default_factory=list)
    # 内容完全未变、原样保留的旧草稿
    kept: list[dict[str, Any]] = field(default_factory=list)
    # 需要删除的旧草稿
    removed: list[dict[str, Any]] = field(default_factory=list)
    # 需要新建的草稿
    added: list[RenderedDraft] = field(default_factory=list)
    # 本次切题得到的全部草稿（含 kept），按页码/顺序排列
    new_numbers: list[str] = field(default_factory=list)
    blocking: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: ImportDiagnostics = field(default_factory=ImportDiagnostics)

    @property
    def affected_pages(self) -> list[int]:
        return list(range(self.start_page, self.end_page + 1))


# ── 页文本 ──

def _effective_text(page: dict[str, Any]) -> str:
    """切题输入优先用人工修正后的整页 Markdown。"""
    edited = page.get("edited_markdown")
    return edited if edited else page.get("raw_markdown", "")


def _page_texts(pages: list[dict[str, Any]]) -> dict[int, str]:
    return {page["page_number"]: _effective_text(page) for page in pages}


def compute_affected_range(
    page_texts: dict[int, str], target_page: int
) -> tuple[int, int]:
    """返回包含 target_page 的"跨页闭合段" [start, end]。

    向前：只要本页存在 preamble，说明内容来自上一页的 pending，必须一起重切。
    向后：只要下一页存在 preamble，说明本段内容延伸过去了，必须一起重切。
    """
    has_preamble = {
        number: page_has_continuation(text) for number, text in page_texts.items()
    }

    start = target_page
    while (start - 1) in page_texts and has_preamble.get(start, False):
        start -= 1

    end = target_page
    while (end + 1) in page_texts and has_preamble.get(end + 1, False):
        end += 1

    return start, end


def _merge_ranges(*ranges: tuple[int, int]) -> tuple[int, int]:
    return min(item[0] for item in ranges), max(item[1] for item in ranges)


def _resolve_range(
    old_texts: dict[int, str], new_texts: dict[int, str], target_page: int
) -> tuple[int, int]:
    """同时按旧文本与新文本求闭合段，并取并集直到稳定。

    旧文本决定"哪些旧草稿被这次修改影响"，新文本决定"新结果需要哪些页做输入"，
    两者都必须覆盖，否则会出现漏删或漏切。
    """
    start, end = _merge_ranges(
        compute_affected_range(old_texts, target_page),
        compute_affected_range(new_texts, target_page),
    )
    while True:
        expanded = (start, end)
        for texts in (old_texts, new_texts):
            for page in (start, end):
                expanded = _merge_ranges(
                    expanded, compute_affected_range(texts, page)
                )
        if expanded == (start, end):
            return start, end
        start, end = expanded


# ── 计划 ──

def _preview_text(markdown: str) -> str:
    body = " ".join(markdown.split())
    return body[:PREVIEW_CHARS]


def _draft_summary(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": draft["question_id"],
        "page_number": draft["page_number"],
        "original_number": draft["original_number"],
        "review_status": draft["review_status"],
        "has_manual_edit": draft["edited_markdown"] != draft["ocr_markdown"],
        "preview": _preview_text(draft["edited_markdown"] or draft["ocr_markdown"]),
    }


def _rendered_summary(draft: RenderedDraft) -> dict[str, Any]:
    return {
        "page_number": draft.page_number,
        "original_number": draft.original_number,
        "preview": _preview_text(draft.markdown),
    }


def _build_draft_diff(
    old_drafts: list[dict[str, Any]],
    rendered: list[RenderedDraft],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[RenderedDraft], list[dict[str, Any]]]:
    """计算未发布草稿的重建差异，并始终原样保留已发布题。"""
    remaining = list(old_drafts)
    kept: list[dict[str, Any]] = []
    added: list[RenderedDraft] = []
    blocking: list[dict[str, Any]] = []
    for draft in rendered:
        match = next((item for item in remaining if
            item["page_number"] == draft.page_number
            and item["original_number"] == draft.original_number
            and item["ocr_markdown"] == draft.markdown), None)
        if match is not None:
            remaining.remove(match)
            kept.append(match)
            continue
        protected = next((item for item in remaining if
            item["page_number"] == draft.page_number
            and item["original_number"] == draft.original_number
            and item["review_status"] == "published"), None)
        if protected is not None:
            remaining.remove(protected)
            kept.append(protected)
            continue
        added.append(draft)

    protected_remaining = [
        item for item in remaining if item["review_status"] == "published"
    ]
    kept.extend(protected_remaining)
    removed = [item for item in remaining if item not in protected_remaining]
    return kept, removed, added, blocking


def build_plan(
    db: WorkbenchDatabase,
    source_file_id: str,
    target_page: int,
    new_markdown: str,
) -> ResplitPlan:
    """只读地计算一次重新切题的完整计划。不写任何数据。"""
    pages = db.list_pages(source_file_id)
    if not pages:
        raise ResplitError("该来源尚未保存整页 Markdown，无法重新切题。")

    old_texts = _page_texts(pages)
    if target_page not in old_texts:
        raise ResplitError(f"第 {target_page} 页没有整页 Markdown 记录。")

    new_texts = dict(old_texts)
    new_texts[target_page] = new_markdown

    source = db.get_source(source_file_id)
    layout = DocumentLayout.from_dict(source.get("layout"), available_pages=sorted(new_texts))
    if layout.solution_mode == "separate":
        layout.validate(set(new_texts))
        processed_pages = sorted(set(layout.question_pages + layout.solution_pages))
        start, end = min(processed_pages), max(processed_pages)
        affected = list(layout.question_pages)
        pipeline_layout = layout
        pipeline_pages = [(number, new_texts[number]) for number in processed_pages]
    else:
        start, end = _resolve_range(old_texts, new_texts, target_page)
        affected = list(range(start, end + 1))
        pipeline_layout = DocumentLayout("inline", affected, [])
        pipeline_pages = [(number, new_texts[number]) for number in affected if number in new_texts]

    result = import_document(pipeline_pages, pipeline_layout)
    rendered: list[RenderedDraft] = []
    for item in result.candidates:
        rendered.extend(render_drafts(item))

    old_drafts = db.list_questions_by_pages(source_file_id, affected)
    kept, removed, added, blocking = _build_draft_diff(old_drafts, rendered)

    return ResplitPlan(
        source_file_id=source_file_id,
        target_page=target_page,
        start_page=start,
        end_page=end,
        old_drafts=old_drafts,
        kept=kept,
        removed=removed,
        added=added,
        new_numbers=[draft.original_number for draft in rendered],
        blocking=blocking,
        diagnostics=result.diagnostics,
    )


def plan_to_dict(plan: ResplitPlan) -> dict[str, Any]:
    return {
        "source_file_id": plan.source_file_id,
        "target_page": plan.target_page,
        "affected_range": {
            "start_page": plan.start_page,
            "end_page": plan.end_page,
            "pages": plan.affected_pages,
            "cross_page": plan.start_page != plan.end_page,
            "description": (
                f"编辑第 {plan.target_page} 页，实际影响第 "
                f"{plan.start_page}-{plan.end_page} 页"
                if plan.start_page != plan.end_page
                else f"仅影响第 {plan.target_page} 页"
            ),
        },
        "old_drafts": [_draft_summary(item) for item in plan.old_drafts],
        "old_numbers": [item["original_number"] for item in plan.old_drafts],
        "new_candidates": [_rendered_summary(item) for item in plan.added]
        + [
            {
                "page_number": item["page_number"],
                "original_number": item["original_number"],
                "preview": _preview_text(item["ocr_markdown"]),
            }
            for item in plan.kept
        ],
        "new_numbers": plan.new_numbers,
        "changes": {
            "added": [_rendered_summary(item) for item in plan.added],
            "removed": [_draft_summary(item) for item in plan.removed],
            "kept": [_draft_summary(item) for item in plan.kept],
        },
        "manual_edits_lost": [
            _draft_summary(item) for item in plan.removed if item["edited_markdown"] != item["ocr_markdown"]
        ],
        "blocked": bool(plan.blocking),
        "blocking_drafts": [_draft_summary(item) for item in plan.blocking],
        "diagnostics": {
            "ambiguous_keys": plan.diagnostics.ambiguous_keys,
            "unmatched_solutions": [
                {"section": item.key[0], "original_number": item.key[1], "page_number": item.page_number}
                for item in plan.diagnostics.unmatched_solutions
            ],
            "missing_questions": [
                {"section": item.key[0], "original_number": item.key[1], "page_number": item.page_number}
                for item in plan.diagnostics.missing_questions
            ],
        },
    }


def apply_plan(
    db: WorkbenchDatabase,
    source_file_id: str,
    target_page: int,
    new_markdown: str,
    *,
    expected_numbers: list[str] | None = None,
) -> dict[str, Any]:
    """在调用方的事务里执行 replace/rebuild。

    调用方必须把本函数包在单个 session 事务中，异常时整体回滚。
    """
    plan = build_plan(db, source_file_id, target_page, new_markdown)

    if expected_numbers is not None and expected_numbers != plan.new_numbers:
        raise ResplitStaleError(expected_numbers, plan.new_numbers)

    if plan.blocking:
        raise ResplitBlockedError([_draft_summary(item) for item in plan.blocking])

    removed_ids = [item["question_id"] for item in plan.removed]
    deleted = db.delete_questions(removed_ids)

    created: list[str] = []
    for draft in plan.added:
        created.append(
            persist_rendered_draft(db, source_file_id=source_file_id, draft=draft)
        )

    db.save_page_markdown(source_file_id, target_page, new_markdown)

    return {
        "affected_range": {
            "start_page": plan.start_page,
            "end_page": plan.end_page,
            "pages": plan.affected_pages,
        },
        "deleted_count": deleted,
        "created_count": len(created),
        "kept_count": len(plan.kept),
        "new_numbers": plan.new_numbers,
        "created_question_ids": created,
    }
