"""OCR 题目导入服务 — 负责 Markdown 解析、字段映射、校验、正式发布。

职责分离：
- workbench/database.py → OcrImportSource / OcrImportDraft 的 CRUD
- 本模块 → 业务逻辑：Markdown 解析 → 字段映射 → 发布到 QuestionDraft + Question
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calculus_agent.questions.chapter_assignment import (
    chapter_display_name,
    derive_default_chapter_from_knowledge,
    question_chapter_display,
)

from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    OcrImportDraft,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)

logger = logging.getLogger(__name__)

# Markdown 模板结构：## 题目内容 / ## 参考解答（兼容旧 ## 题目/选项/答案/解析）
SECTION_RE = re.compile(r"(?m)^##\s+(题目内容|参考解答|题目|选项|答案|解析|题型|章节|知识点|难度|来源页码|原始题号|审核备注)\b")


def parse_markdown_to_parts(markdown: str) -> dict[str, str]:
    """从固定模板 Markdown 中提取各个字段。

    返回 dict，key 包括：题目, 选项, 答案, 解析, 题型, 章节, 知识点, 难度 等。
    """
    sections = list(SECTION_RE.finditer(markdown))
    parts: dict[str, str] = {}
    for idx, match in enumerate(sections):
        key = match.group(1)
        start = match.end()
        end = sections[idx + 1].start() if idx + 1 < len(sections) else len(markdown)
        parts[key] = markdown[start:end].strip()
    return parts


def publish_ocr_draft(
    session: Session,
    draft: OcrImportDraft,
    *,
    subject: str = "高等数学",
    grade: str | None = None,
    publish_source: str = "manual",
    ai_review_result: dict[str, Any] | None = None,
    quality_sample_required: bool = False,
) -> dict[str, Any] | None:
    """将已审核的 OcrImportDraft 发布到正式题库。

    新版数据流：
    1. 解析 edited_markdown → question_content / solution_content
    2. 创建 QuestionDraft → Question
    """
    parts = parse_markdown_to_parts(draft.edited_markdown)

    # 新版两段式结构
    question_content = parts.get("题目内容", "")
    solution_content = parts.get("参考解答", "")

    # 兼容旧格式回退
    if not question_content:
        stem = parts.get("题目", "")
        opts = parts.get("选项", "")
        question_content = (stem + "\n\n" + opts).strip() if opts else stem

    if not solution_content:
        ans = parts.get("答案", "")
        ana = parts.get("解析", "")
        parts_list = [p for p in [ans, ana] if p]
        solution_content = "\n\n".join(parts_list)

    if not question_content.strip():
        return None  # 没有题目内容，无法发布

    question_type = parts.get("题型", "calculation").strip()
    if not question_type:
        question_type = "calculation"

    # ── 结构化元数据：知识点 / 难度 ──
    # 来自 OCR 审核阶段写入数据库字段的知识 id 列表与难度，不再从 Markdown 的
    # ## 知识点 / ## 难度 section 反解（那些 section 已从 OCR 模板中移除）。
    knowledge_ids = [item for item in (draft.knowledge_points_json or []) if item][:3]
    difficulty_level = draft.difficulty_level  # 1~5 或 None
    default_chapter = derive_default_chapter_from_knowledge(
        session, knowledge_ids
    )

    # 去重检查
    existing = session.scalar(
        select(QuestionDraft).where(
            QuestionDraft.source_name == "ocr_import",
            QuestionDraft.source_item_id == draft.id,
        )
    )
    if existing is not None:
        if existing.status == "approved":
            question = session.scalar(
                select(Question).where(Question.draft_id == existing.id)
            )
            if question is not None:
                draft.review_status = "published"
                return {"draft_id": existing.id, "question_id": question.id, "cached": True}
        return None

    normalized_fingerprint = hashlib.sha256(
        question_content.encode("utf-8")
    ).hexdigest()

    question_draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id=draft.id,
        variant=1,
        subject=subject,
        language="zh-CN",
        grade=grade,
        question_type=question_type,
        # Compatibility display mirror. Authoritative scope is stored on
        # Question.curriculum_chapter_id.
        source_topic=chapter_display_name(default_chapter) or parts.get("章节"),
        question_text=question_content,
        reference_answers_json=[],
        answer_types_json=[],
        options_json=[],
        solution_text=solution_content or None,
        level=str(difficulty_level) if difficulty_level else "medium",
        keywords_json=[],
        normalized_fingerprint=normalized_fingerprint,
        status="approved",
        solver_result_json={"source": "ocr_import"},
        verification_result_json={
            "status": "ai_verified" if publish_source == "ai_auto" else "manual_verified",
            "source": publish_source,
            "ai_review": ai_review_result,
        },
    )
    session.add(question_draft)
    session.flush()

    # A revision draft keeps the formal question identity. Update that row
    # instead of creating a second formal question for the same exercise.
    question = session.get(Question, draft.formal_question_id) if draft.formal_question_id else None
    if question is None:
        question = Question(
            draft_id=question_draft.id,
            curriculum_chapter_id=(
                default_chapter.id if default_chapter else None
            ),
            question_text=question_content,
            grade=grade,
            question_type=question_type,
            final_answer=None,
            solution_json={
                "solution_steps": [solution_content] if solution_content else []
            },
            verification_status=(
                "ai_verified" if publish_source == "ai_auto"
                else "manual_verified"
            ),
            review_status="approved",
        )
        session.add(question)
        session.flush()
    else:
        question.draft_id = question_draft.id
        question.question_text = question_content
        question.question_type = question_type
        question.solution_json = {"solution_steps": [solution_content] if solution_content else []}
        question.review_status = "approved"
        # Revision preserves a teacher-confirmed owner chapter.
        if (
            question.curriculum_chapter_id is None
            and default_chapter is not None
        ):
            question.curriculum_chapter_id = default_chapter.id

    published_at = datetime.now(UTC)
    question.publish_source = publish_source
    question.ai_review_json = ai_review_result
    question.quality_sample_required = quality_sample_required
    question.published_at = published_at

    # ── 同步结构化关系：知识点关联 + 难度画像 ──
    # publish 之后题库按 QuestionKnowledgeLink / QuestionProfile 展示，
    # 不再依赖 OCR Markdown 中的任何章节 / 知识点 / 难度 section。
    _sync_knowledge_links(
        session,
        question,
        knowledge_ids,
        source=publish_source,
        confidence=float((ai_review_result or {}).get("confidence") or 1.0),
    )
    _sync_publish_profile(
        session,
        question,
        knowledge_ids,
        difficulty_level,
        source=publish_source,
    )

    draft.formal_question_id = question.id
    draft.publish_source = publish_source
    draft.ai_review_json = ai_review_result
    draft.quality_sample_required = quality_sample_required
    draft.published_at = published_at
    draft.review_status = "published"
    return {"draft_id": question_draft.id, "question_id": question.id, "cached": False}


def derive_chapter_from_knowledge(session: Session, knowledge_ids: list[str]) -> str | None:
    """OCR/import **provenance** helper for ``QuestionDraft.source_topic``.

    IMPORTANT: this is NOT the formal business chapter. Formal chapter
    derivation now lives in
    ``calculus_agent.knowledge.chapter.resolve_chapter_from_knowledge_ids``
    and is consumed by the question-bank API. This helper only produces a
    human-readable original-topic string for OCR/import metadata; callers must
    never treat its result as the authoritative question chapter.

    Returns the single latest chapter name (by textbook order) or None.
    """
    from calculus_agent.knowledge.chapter import resolve_chapter_from_knowledge_ids

    return resolve_chapter_from_knowledge_ids(session, knowledge_ids).chapter_name


def apply_ai_published_profile_review(
    session: Session,
    draft: OcrImportDraft | None,
    *,
    primary_knowledge_point_id: str,
    secondary_knowledge_point_ids: list[str],
    difficulty_level: int,
    modification_reason: str | None = None,
) -> None:
    """复核 AI 已发布题的画像；正文保持封板，AI 原始快照保持可追溯。"""
    if draft is None:
        raise KeyError("OCR 草稿不存在")
    if draft.review_status != "published" or draft.publish_source != "ai_auto":
        raise ValueError("仅 AI 自动发布题支持画像复核")
    if difficulty_level not in {1, 2, 3, 4, 5}:
        raise ValueError("难度必须为 1～5")
    secondary = list(dict.fromkeys(secondary_knowledge_point_ids))
    if len(secondary) > 2:
        raise ValueError("辅助知识点最多 2 个")
    if primary_knowledge_point_id in secondary:
        raise ValueError("主知识点不能同时作为辅助知识点")
    knowledge_ids = [primary_knowledge_point_id, *secondary]
    if any(session.get(KnowledgeNode, item) is None for item in knowledge_ids):
        raise ValueError("知识点不存在")

    question = session.get(Question, draft.formal_question_id)
    if question is None:
        raise ValueError("AI 自动发布题缺少正式题记录")
    bank_draft = session.get(QuestionDraft, question.draft_id)
    if bank_draft is None:
        raise ValueError("正式题缺少来源草稿")

    reviewed_at = datetime.now(UTC)
    shadow = dict(draft.knowledge_shadow_json or {})
    ai = shadow.get("ai") if isinstance(shadow.get("ai"), dict) else {}
    ai_primary = ai.get("primary_knowledge_point_id")
    ai_secondary = list(ai.get("secondary_knowledge_point_ids") or [])
    modified = (
        ai_primary != primary_knowledge_point_id or ai_secondary != secondary
    )
    shadow["human"] = {
        "primary_knowledge_point_id": primary_knowledge_point_id,
        "secondary_knowledge_point_ids": secondary,
        "difficulty_level": difficulty_level,
        "modified": modified,
        "modification_reason": (
            modification_reason.strip()
            if modification_reason
            else ("manual_adjustment" if modified else "ai_accepted")
        ),
        "reviewed_at": reviewed_at.isoformat(),
    }
    draft.knowledge_shadow_json = shadow
    draft.knowledge_points_json = knowledge_ids
    draft.difficulty_level = difficulty_level

    _sync_knowledge_links(
        session,
        question,
        knowledge_ids,
        source="ai_auto_human_review",
        confidence=1.0,
    )
    previous_profile = session.scalar(
        select(QuestionProfile)
        .where(QuestionProfile.question_id == question.id)
        .order_by(QuestionProfile.profile_version.desc())
    )
    knowledge_count = len(knowledge_ids)
    session.add(QuestionProfile(
        question_id=question.id,
        profile_version=(previous_profile.profile_version + 1) if previous_profile else 1,
        difficulty=difficulty_level,
        estimated_time_min={1: 2, 2: 4, 3: 7, 4: 11, 5: 15}[difficulty_level],
        reasoning_depth=previous_profile.reasoning_depth if previous_profile else 2,
        calculation_load=previous_profile.calculation_load if previous_profile else 2,
        knowledge_depth=1 + (knowledge_count >= 2) + (knowledge_count >= 3),
        comprehensive_level=1 + (knowledge_count >= 2) + (knowledge_count >= 3),
        confidence=1.0,
        profile_source="human",
        profile_status="approved",
        reason="AI 自动发布后的教师画像复核",
        reviewed_at=reviewed_at,
    ))
    # Knowledge review never reassigns owning chapter.
    bank_draft.source_topic = question_chapter_display(session, question)
    question.knowledge_match_status = "current"

    audit = dict(draft.ai_review_json or {})
    audit["profile_human_review"] = shadow["human"]
    draft.ai_review_json = audit
    question.ai_review_json = audit
    session.flush()


def _sync_knowledge_links(
    session: Session,
    question: Question,
    knowledge_ids: list[str],
    *,
    source: str = "manual",
    confidence: float = 1.0,
) -> None:
    """覆盖式同步 QuestionKnowledgeLink：删除旧关联，按结构化 knowledge_id 重建。

    所有知识点都是等价关联；relation_type 仅作为历史 schema 的兼容字段。
    只链接库中真实存在的 KnowledgeNode，避免脏 id 造成外键错误。
    """
    session.execute(
        delete(QuestionKnowledgeLink).where(QuestionKnowledgeLink.question_id == question.id)
    )
    if not knowledge_ids:
        return
    nodes = {
        item.id: item
        for item in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.id.in_(knowledge_ids))
        ).all()
    }
    for node_id in dict.fromkeys(knowledge_ids):
        if nodes.get(node_id) is None:
            continue
        session.add(QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=node_id,
            relation_type="related",
            confidence=max(0.0, min(confidence, 1.0)),
            evidence_json=[{
                "source": (
                    "ai_auto_human_review"
                    if source == "ai_auto_human_review"
                    else ("ai_auto" if source == "ai_auto" else "ocr_review")
                ),
                "basis": (
                    "AI 自动发布后由教师复核"
                    if source == "ai_auto_human_review"
                    else ("AI 受控自动发布" if source == "ai_auto" else "OCR 审核阶段教师确认")
                ),
            }],
        ))


def _sync_publish_profile(
    session: Session,
    question: Question,
    knowledge_ids: list[str],
    difficulty_level: int | None,
    *,
    source: str = "manual",
) -> None:
    """覆盖式同步难度画像：教师在 OCR 审核阶段设定难度后发布，直接 approved。

    题库难度展示依赖最新 approved 的 QuestionProfile.difficulty；
    不复用自动预标流程，保留教师的判断，同时给出合理的关联深度等派生指标。
    """
    session.execute(
        delete(QuestionProfile).where(QuestionProfile.question_id == question.id)
    )
    difficulty = difficulty_level if difficulty_level in (1, 2, 3, 4, 5) else 3
    knowledge_count = len(knowledge_ids)
    session.add(QuestionProfile(
        question_id=question.id,
        profile_version=1,
        difficulty=difficulty,
        estimated_time_min={1: 2, 2: 4, 3: 7, 4: 11, 5: 15}.get(difficulty, 7),
        reasoning_depth=2,
        calculation_load=2,
        knowledge_depth=1 + (knowledge_count >= 2) + (knowledge_count >= 3),
        comprehensive_level=1 + (knowledge_count >= 2) + (knowledge_count >= 3),
        confidence=0.9,
        # OCR 审核阶段的难度由教师人工设定并确认，属 human 来源；
        # "ocr_publish" 是业务流程来源，不在 profile_source 维度内。
        profile_source="auto" if source == "ai_auto" else "human",
        profile_status="approved",
        reason=(
            "受控自动发布：确定性画像完整性校验通过。"
            if source == "ai_auto"
            else "OCR 审核阶段由教师设定难度后发布，跳过自动预标。"
        ),
    ))
