import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import CurriculumNode, KnowledgeNode
from calculus_agent.knowledge.normalization import normalize_name


_CHAPTER = re.compile(r"^(第[一二三四五六七八九十百]+章)\s*(.+)$")
_SECTION = re.compile(r"^(?:第[一二三四五六七八九十百]+节|\d+(?:\.\d+)+)\s*(.+)$")


def import_curriculum(session: Session, text: str) -> list[CurriculumNode]:
    nodes: list[CurriculumNode] = []
    parent: CurriculumNode | None = None
    for order, raw in enumerate(text.splitlines()):
        line = raw.strip().lstrip("-•* ")
        if not line:
            continue
        chapter = _CHAPTER.match(line)
        section = _SECTION.match(line)
        if chapter:
            node_type, code, title, parent_id = "chapter", chapter.group(1), line, None
        elif section:
            node_type, code, title, parent_id = "section", None, line, parent.id if parent else None
        else:
            node_type, code, title, parent_id = "topic", None, line, parent.id if parent else None
        node = CurriculumNode(
            parent_id=parent_id,
            node_type=node_type,
            code=code,
            title=title,
            sort_order=order,
        )
        session.add(node)
        session.flush()
        nodes.append(node)
        if node_type == "chapter":
            parent = node
        concept_name = _concept_name(title)
        if concept_name:
            session.add(
                KnowledgeNode(
                    curriculum_node_id=node.id,
                    node_type="concept",
                    name=concept_name,
                    normalized_name=normalize_name(concept_name),
                    source_type="directory",
                    confidence=1.0,
                    review_status="approved",
                )
            )
    session.flush()
    return nodes


def _concept_name(title: str) -> str:
    value = re.sub(r"^第[一二三四五六七八九十百]+章\s*", "", title)
    value = re.sub(r"^(?:第[一二三四五六七八九十百]+节|\d+(?:\.\d+)+)\s*", "", value)
    return value.strip()


def sync_directory_knowledge_nodes(
    session: Session,
    nodes: list[CurriculumNode],
    *,
    reusable_nodes: list[KnowledgeNode] | None = None,
) -> list[KnowledgeNode]:
    """Make textbook directory entries available as approved knowledge points.

    A replacement import may pass the previous directory-backed knowledge nodes.
    Matching names keep their stable IDs so published question links are not broken.
    Previous entries that disappeared from the directory are retired by the caller.
    """
    curriculum_ids = [node.id for node in nodes]
    linked = {
        item.curriculum_node_id: item
        for item in session.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.curriculum_node_id.in_(curriculum_ids),
                KnowledgeNode.source_type == "directory",
            )
        ).all()
    } if curriculum_ids else {}
    reusable_by_name = {
        normalize_name(item.name): item
        for item in (reusable_nodes or [])
        if item.source_type == "directory"
    }
    synced: list[KnowledgeNode] = []
    used_ids: set[str] = set()
    for curriculum in nodes:
        name = curriculum.title.strip()
        if not name:
            continue
        normalized = normalize_name(name)
        knowledge = linked.get(curriculum.id) or reusable_by_name.get(normalized)
        if knowledge is None or knowledge.id in used_ids:
            knowledge = KnowledgeNode(
                curriculum_node_id=curriculum.id,
                node_type="concept",
                name=name,
                normalized_name=_directory_normalized_name(
                    session,
                    normalized,
                    curriculum.id,
                ),
                description="由当前教材章节目录生成",
                source_type="directory",
                confidence=1.0,
                review_status="approved",
            )
            session.add(knowledge)
        else:
            knowledge.curriculum_node_id = curriculum.id
            knowledge.name = name
            knowledge.normalized_name = _directory_normalized_name(
                session,
                normalized,
                curriculum.id,
                current_id=knowledge.id,
            )
            knowledge.review_status = "approved"
        session.flush()
        used_ids.add(knowledge.id)
        synced.append(knowledge)
    return synced


def _directory_normalized_name(
    session: Session,
    normalized: str,
    curriculum_node_id: str,
    *,
    current_id: str | None = None,
) -> str:
    """Honor the legacy global uniqueness constraint without sharing nodes across books."""
    collision = session.scalar(
        select(KnowledgeNode.id).where(
            KnowledgeNode.node_type == "concept",
            KnowledgeNode.normalized_name == normalized,
            *([KnowledgeNode.id != current_id] if current_id else []),
        ).limit(1)
    )
    return normalized if collision is None else f"{normalized}::{curriculum_node_id}"


def retire_directory_knowledge_nodes(
    session: Session,
    nodes: list[KnowledgeNode],
    *,
    keep_ids: set[str] | None = None,
) -> None:
    """Retire removed directory entries without deleting historical question links."""
    keep = keep_ids or set()
    for node in nodes:
        if node.id in keep:
            continue
        node.curriculum_node_id = None
        node.review_status = "retired"
    session.flush()
