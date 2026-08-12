from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.normalization import normalize_name, terms
from calculus_agent.models import KnowledgeAlias, KnowledgeNode


@dataclass(frozen=True)
class KnowledgeMatch:
    node: KnowledgeNode
    score: float
    reasons: list[str]


def retrieve_knowledge(
    session: Session,
    query: str,
    *,
    node_type: str | None = None,
    limit: int = 10,
) -> list[KnowledgeMatch]:
    statement = select(KnowledgeNode).where(KnowledgeNode.review_status == "approved")
    if node_type:
        statement = statement.where(KnowledgeNode.node_type == node_type)
    nodes = session.scalars(statement).all()
    aliases = session.scalars(select(KnowledgeAlias)).all()
    alias_by_node: dict[str, list[str]] = {}
    for alias in aliases:
        alias_by_node.setdefault(alias.node_id, []).append(alias.alias)
    query_terms = terms(query)
    compact_query = normalize_name(query)
    matches = []
    for node in nodes:
        candidates = [node.name, *alias_by_node.get(node.id, [])]
        score = 0.0
        reasons: list[str] = []
        for name in candidates:
            compact_name = normalize_name(name)
            overlap = _jaccard(query_terms, terms(name))
            current = overlap * 0.7
            if compact_name and compact_name in compact_query:
                current += 0.3
                reasons.append("名称直接出现")
            if overlap > 0:
                reasons.append("术语重合")
            score = max(score, current)
        if score > 0:
            matches.append(
                KnowledgeMatch(
                    node=node, score=round(min(score, 1.0), 4), reasons=list(dict.fromkeys(reasons))
                )
            )
    matches.sort(key=lambda item: (-item.score, item.node.name))
    return matches[:limit]


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
