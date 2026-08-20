"""Read-only hybrid retrieval over the current curriculum taxonomy.

Retrieval produces candidates only. It never resolves or writes teaching scope.
"""

from __future__ import annotations

from collections import defaultdict
import math

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.normalization import normalize_name, terms
from calculus_agent.knowledge.rag.embedding import (
    EmbeddingProvider,
    get_embedding_provider,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeAlias,
    KnowledgeNode,
    Textbook,
)


class CurriculumCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    title: str
    parent_path: list[str] = Field(default_factory=list)
    similarity_score: float = Field(ge=0.0, le=1.0)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(
        sum(x * x for x in right)
    )
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, sum(x * y for x, y in zip(left, right)) / denominator))


def _lexical_score(query: str, labels: list[str]) -> float:
    normalized_query = normalize_name(query)
    query_terms = terms(query)
    best = 0.0
    for label in labels:
        normalized_label = normalize_name(label)
        if not normalized_label:
            continue
        if normalized_label in normalized_query:
            best = max(best, 1.0)
            continue
        label_terms = terms(label)
        union = query_terms | label_terms
        if union:
            best = max(best, len(query_terms & label_terms) / len(union))
    return best


def retrieve_curriculum_candidates(
    session: Session,
    *,
    query: str,
    top_k: int = 5,
    embedding_provider: EmbeddingProvider | None = None,
) -> list[CurriculumCandidate]:
    """Return ranked taxonomy candidates without selecting teaching scope."""
    query = query.strip()
    if not query or top_k < 1:
        return []
    top_k = min(top_k, 20)

    active_textbook_ids = set(session.scalars(
        select(Textbook.id).where(Textbook.is_active.is_(True))
    ).all())
    node_statement = select(CurriculumNode).where(
        CurriculumNode.review_status == "approved"
    )
    if active_textbook_ids:
        node_statement = node_statement.where(
            CurriculumNode.textbook_id.in_(active_textbook_ids)
        )
    curriculum = list(session.scalars(node_statement).all())
    if not curriculum:
        return []

    node_by_id = {node.id: node for node in curriculum}

    def path_for(node_id: str | None) -> list[str]:
        path: list[str] = []
        seen: set[str] = set()
        current = node_id
        while current and current not in seen:
            seen.add(current)
            node = node_by_id.get(current)
            if node is None:
                break
            path.append(node.title)
            current = node.parent_id
        return list(reversed(path))

    knowledge = list(session.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.review_status == "approved",
            KnowledgeNode.curriculum_node_id.in_(node_by_id),
        )
    ).all())
    aliases_by_node: dict[str, list[str]] = defaultdict(list)
    if knowledge:
        for alias in session.scalars(
            select(KnowledgeAlias).where(
                KnowledgeAlias.node_id.in_([item.id for item in knowledge])
            )
        ).all():
            aliases_by_node[alias.node_id].append(alias.alias)

    descendant_labels: dict[str, list[str]] = defaultdict(list)
    for item in knowledge:
        current = item.curriculum_node_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            descendant_labels[current].extend([
                item.name,
                *aliases_by_node[item.id],
            ])
            parent = node_by_id.get(current)
            current = parent.parent_id if parent else None

    records: list[tuple[CurriculumCandidate, str, float]] = []
    for node in curriculum:
        parent_path = path_for(node.parent_id)
        labels = [node.title, node.code or "", *descendant_labels[node.id]]
        text = " / ".join([*parent_path, node.title, *descendant_labels[node.id][:30]])
        lexical = _lexical_score(query, labels)
        records.append((CurriculumCandidate(
            node_id=node.id,
            node_type=node.node_type,
            title=node.title,
            parent_path=parent_path,
            similarity_score=0.0,
        ), text, lexical))

    for item in knowledge:
        parent_path = path_for(item.curriculum_node_id)
        labels = [item.name, item.description or "", *aliases_by_node[item.id]]
        text = " / ".join([*parent_path, item.name, item.description or "", *aliases_by_node[item.id]])
        records.append((CurriculumCandidate(
            node_id=item.id,
            node_type=item.node_type,
            title=item.name,
            parent_path=parent_path,
            similarity_score=0.0,
        ), text, _lexical_score(query, labels)))

    provider = embedding_provider or get_embedding_provider()
    query_vector = provider.embed_query(query)
    vectors = provider.embed_documents([text for _, text, _ in records])
    ranked: list[CurriculumCandidate] = []
    for (candidate, _text, lexical), vector in zip(records, vectors):
        semantic = _cosine(query_vector, vector)
        candidate.similarity_score = round(max(lexical, 0.65 * semantic + 0.35 * lexical), 6)
        ranked.append(candidate)

    type_priority = {"chapter": 3, "section": 2}
    ranked.sort(
        key=lambda item: (
            -item.similarity_score,
            -type_priority.get(item.node_type, 1),
            len(item.parent_path),
            item.title,
            item.node_id,
        )
    )
    return ranked[:top_k]
