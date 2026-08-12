from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.retrieval import retrieve_knowledge
from calculus_agent.models import KnowledgeNode, QuestionDraft
from calculus_agent.schemas import ClassificationCandidate, DraftProcessRead
from calculus_agent.solver.service import Solver
from calculus_agent.verifier.service import verify_answer


class DraftNotFoundError(LookupError):
    pass


def process_draft(session: Session, draft_id: str, solver: Solver) -> DraftProcessRead:
    draft = session.get(QuestionDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError(draft_id)
    query = " ".join(
        item
        for item in (
            draft.question_text,
            draft.source_topic,
            draft.source_subtopic,
            " ".join(draft.keywords_json),
        )
        if item
    )
    matches = retrieve_knowledge(session, query, limit=12)
    solution = solver.solve(draft.question_text, [item.node.name for item in matches])
    verification = verify_answer(solution.final_answer, draft.reference_answers_json)
    second_query = " ".join(
        [query, *solution.used_knowledge, *solution.used_methods, *solution.solution_steps]
    )
    final_matches = retrieve_knowledge(session, second_query, limit=12)
    # Keep legacy retrieval candidates for the existing draft review response;
    # the new directory classifier is only invoked explicitly by its tool/API.
    candidates = [
        ClassificationCandidate(knowledge_node_id=item.node.id, name=item.node.name,
                                node_type=item.node.node_type, score=item.score,
                                evidence=item.reasons)
        for item in final_matches
    ]
    draft.solver_result_json = solution.model_dump()
    draft.verification_result_json = verification.model_dump()
    draft.proposed_classification_json = {"candidates": [item.model_dump() for item in candidates]}
    draft.status = "ready_for_review" if verification.status == "verified" else "needs_review"
    session.flush()
    return DraftProcessRead(
        draft_id=draft.id,
        status=draft.status,
        solution=solution,
        verification=verification,
        candidates=candidates,
    )


def require_approved_nodes(session: Session, node_ids: list[str]) -> list[KnowledgeNode]:
    if not node_ids:
        return []
    nodes = session.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.id.in_(set(node_ids)), KnowledgeNode.review_status == "approved"
        )
    ).all()
    if len(nodes) != len(set(node_ids)):
        raise ValueError("All selected knowledge nodes must exist and be approved")
    return nodes
