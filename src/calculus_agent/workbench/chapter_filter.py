"""题库按教材一级章节（大章节）筛选的确定性逻辑。

数据链路（全部为数据库 / Python 确定性计算，绝不调用 LLM）：

    Question(OcrImportDraft).knowledge_points_json   # 已确认知识点 id 列表
        → KnowledgeNode.id
        → KnowledgeNode.curriculum_node_id           # 该知识点所属目录节点
        → CurriculumNode 父链（parent_id）            # 向上回溯到一级章节
        → node_type == "chapter"                      # 一级章节

一道题只要任意一个已确认知识点落在目标章节（含其全部子节点）即命中；
天然去重（每条题目本身就是唯一记录）。
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def _active_textbook_id(session: Session) -> str | None:
    return session.scalar(
        select(Textbook.id)
        .where(Textbook.is_active.is_(True))
        .order_by(Textbook.created_at.desc(), Textbook.id)
        .limit(1)
    )


def chapter_display_label(node: CurriculumNode) -> str:
    """将一级章节节点渲染为「第一章 函数与极限」形式的可读标签。"""
    code = node.code
    title = node.title or ""
    if code:
        prefix = f"第{code}章"
        if title.startswith(prefix):
            return title
        return f"{prefix} {title}" if title else prefix
    return title or node.id


def list_top_level_chapters(session: Session) -> list[CurriculumNode]:
    """返回当前激活教材的一级章节（node_type='chapter'），按目录原始顺序排列。"""
    textbook_id = _active_textbook_id(session)
    if not textbook_id:
        return []
    return list(session.scalars(
        select(CurriculumNode)
        .where(
            CurriculumNode.textbook_id == textbook_id,
            CurriculumNode.node_type == "chapter",
            CurriculumNode.review_status == "approved",
        )
        .order_by(CurriculumNode.sort_order, CurriculumNode.id)
    ).all())


def chapter_descendant_knowledge_ids(session: Session, chapter_id: str) -> set[str]:
    """返回某一级章节及其全部后代 CurriculumNode 所关联的已审核 KnowledgeNode id 集合。

    chapter_id 必须是 CurriculumNode.id（一级章节）。返回空集合表示章节不存在、
    非一级章节，或不关联任何已审核知识点。
    """
    chapter = session.get(CurriculumNode, chapter_id)
    if chapter is None or chapter.node_type != "chapter":
        return set()

    # 仅加载该章节所属教材的目录节点，避免跨教材污染；构建 parent→children 邻接表后 BFS。
    nodes = list(session.scalars(
        select(CurriculumNode).where(CurriculumNode.textbook_id == chapter.textbook_id)
    ).all())
    children: dict[str | None, list[str]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node.id)

    descendant_ids: set[str] = set()
    queue = [chapter_id]
    while queue:
        current = queue.pop()
        if current in descendant_ids:
            continue
        descendant_ids.add(current)
        queue.extend(children.get(current, []))

    if not descendant_ids:
        return set()

    knowledge = session.scalars(
        select(KnowledgeNode.id).where(
            KnowledgeNode.curriculum_node_id.in_(descendant_ids),
            KnowledgeNode.review_status == "approved",
        )
    ).all()
    return set(knowledge)


def filter_questions_by_chapter(
    items: Iterable[dict],
    descendant_knowledge_ids: set[str],
) -> list[dict]:
    """在已加载的题目列表（每项含 knowledge_points 字段）中保留属于目标章节的题目。

    一道题只要任意一个已确认知识点命中章节后代即保留；天然去重。
    当 descendant_knowledge_ids 为空（无效章节）时返回空列表——调用方应在
    chapter_id 无效时跳过本函数，以保持「无效章节 = 不限制」的语义。
    """
    result: list[dict] = []
    for item in items:
        points = item.get("knowledge_points") or []
        if set(points) & descendant_knowledge_ids:
            result.append(item)
    return result
