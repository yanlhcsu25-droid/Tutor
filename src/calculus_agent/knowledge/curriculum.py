import re

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
