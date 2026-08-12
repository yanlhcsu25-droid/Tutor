"""OCR 题目导入服务 — 负责 Markdown 解析、字段映射、校验、正式发布。

职责分离：
- workbench/database.py → OcrImportSource / OcrImportDraft 的 CRUD
- 本模块 → 业务逻辑：Markdown 解析 → 字段映射 → 发布到 QuestionDraft + Question
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

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
        # 章节优先由全部知识点的教材目录层级反推；
        # 仅当没有结构化知识点时（历史草稿）才回退到 Markdown 章节文本。
        source_topic=derive_chapter_from_knowledge(session, knowledge_ids) or parts.get("章节"),
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
        verification_result_json={"status": "manual_verified", "source": "ocr_import"},
    )
    session.add(question_draft)
    session.flush()

    # A revision draft keeps the formal question identity. Update that row
    # instead of creating a second formal question for the same exercise.
    question = session.get(Question, draft.formal_question_id) if draft.formal_question_id else None
    if question is None:
        question = Question(draft_id=question_draft.id, question_text=question_content, grade=grade, question_type=question_type, final_answer=None, solution_json={"solution_steps": [solution_content] if solution_content else []}, verification_status="manual_verified", review_status="approved")
        session.add(question)
        session.flush()
    else:
        question.draft_id = question_draft.id
        question.question_text = question_content
        question.question_type = question_type
        question.solution_json = {"solution_steps": [solution_content] if solution_content else []}
        question.review_status = "approved"

    # ── 同步结构化关系：知识点关联 + 难度画像 ──
    # publish 之后题库按 QuestionKnowledgeLink / QuestionProfile 展示，
    # 不再依赖 OCR Markdown 中的任何章节 / 知识点 / 难度 section。
    _sync_knowledge_links(session, question, knowledge_ids)
    _sync_publish_profile(session, question, knowledge_ids, difficulty_level)

    draft.formal_question_id = question.id

    draft.review_status = "published"
    return {"draft_id": question_draft.id, "question_id": question.id, "cached": False}


def derive_chapter_from_knowledge(session: Session, knowledge_ids: list[str]) -> str | None:
    """由全部 KnowledgeNode 的教材目录层级反推去重后的章节路径。

    仅用于发布时填写 QuestionDraft.source_topic（题库"章节"展示列）。
    没有结构化知识点时返回 None，调用方回退到 Markdown 章节兼容文本。
    """
    paths: set[str] = set()
    for node_id in dict.fromkeys(knowledge_ids):
        node = session.get(KnowledgeNode, node_id)
        if node is None or not node.curriculum_node_id:
            continue
        titles: list[str] = []
        seen: set[str] = set()
        cur = session.get(CurriculumNode, node.curriculum_node_id)
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            titles.insert(0, cur.title)
            cur = session.get(CurriculumNode, cur.parent_id) if cur.parent_id else None
        if titles:
            # source_topic 是章节字段：只保留所属章，避免同章不同节产生多个章节。
            paths.add(titles[0])
    return "；".join(sorted(paths)) or None


def _sync_knowledge_links(session: Session, question: Question, knowledge_ids: list[str]) -> None:
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
            confidence=1.0,
            evidence_json=[{"source": "ocr_review", "basis": "OCR 审核阶段教师确认"}],
        ))


def _sync_publish_profile(
    session: Session,
    question: Question,
    knowledge_ids: list[str],
    difficulty_level: int | None,
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
        profile_source="human",
        profile_status="approved",
        reason="OCR 审核阶段由教师设定难度后发布，跳过自动预标。",
    ))
