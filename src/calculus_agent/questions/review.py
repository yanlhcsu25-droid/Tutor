from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.steward import require_approved_nodes
from calculus_agent.questions.chapter_assignment import (
    sync_question_chapter_ownership,
)
from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.schemas import DraftApproveRequest, KnowledgeNodeRead, QuestionRead


class DraftApprovalError(ValueError):
    pass


def approve_draft(session: Session, draft_id: str, request: DraftApproveRequest) -> QuestionRead:
    draft = session.get(QuestionDraft, draft_id)
    if draft is None:
        raise DraftApprovalError("Draft not found")
    if not draft.solver_result_json or not draft.verification_result_json:
        raise DraftApprovalError("Draft must be processed before approval")
    if draft.status == "approved":
        question = session.scalar(select(Question).where(Question.draft_id == draft.id))
        if question is None:
            raise DraftApprovalError("Approved draft has no published question")
        return get_question(session, question.id)

    if not request.primary_concept_id:
        raise DraftApprovalError("正式审核必须确认至少一个知识点")
    if len(set([request.primary_concept_id, *request.secondary_concept_ids])) > 3:
        raise DraftApprovalError("知识点最多选择3个")

    relation_ids = {
        "primary_concept": [request.primary_concept_id],
        "secondary_concept": request.secondary_concept_ids,
        "problem_type": request.problem_type_ids,
        "method": request.method_ids,
    }
    all_ids = [item for values in relation_ids.values() for item in values]
    nodes = {node.id: node for node in require_approved_nodes(session, all_ids)}
    if nodes[request.primary_concept_id].node_type != "concept":
        raise DraftApprovalError("Primary knowledge must be a concept node")
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade=draft.grade,
        question_type=draft.question_type,
        final_answer=draft.solver_result_json.get("final_answer"),
        solution_json=draft.solver_result_json
        or {"solution_steps": [draft.solution_text] if draft.solution_text else []},
        verification_status=draft.verification_result_json.get("status", "unsupported"),
        review_status="approved",
    )
    session.add(question)
    session.flush()
    for relation, node_ids in relation_ids.items():
        expected_type = {
            "primary_concept": "concept",
            "secondary_concept": "concept",
            "problem_type": "problem_type",
            "method": "method",
        }[relation]
        for node_id in dict.fromkeys(node_ids):
            if nodes[node_id].node_type != expected_type:
                raise DraftApprovalError(f"{relation} requires a {expected_type} node")
            session.add(
                QuestionKnowledgeLink(
                    question_id=question.id,
                    knowledge_node_id=node_id,
                    relation_type=relation,
                    confidence=1.0,
                    evidence_json=["教师审核确认"],
                )
            )
    draft.status = "approved"
    session.flush()
    sync_question_chapter_ownership(session, question.id)
    return get_question(session, question.id)


def get_question(session: Session, question_id: str) -> QuestionRead:
    question = session.get(Question, question_id)
    if question is None:
        raise DraftApprovalError("Question not found")
    rows = session.execute(
        select(KnowledgeNode, QuestionKnowledgeLink)
        .join(QuestionKnowledgeLink, QuestionKnowledgeLink.knowledge_node_id == KnowledgeNode.id)
        .where(QuestionKnowledgeLink.question_id == question.id)
    ).all()
    return QuestionRead(
        id=question.id,
        draft_id=question.draft_id,
        question_text=question.question_text,
        final_answer=question.final_answer,
        verification_status=question.verification_status,
        knowledge=[
            KnowledgeNodeRead(
                id=node.id,
                node_type=node.node_type,
                name=node.name,
                curriculum_node_id=node.curriculum_node_id,
                review_status=node.review_status,
                match_reasons=[link.relation_type],
            )
            for node, link in rows
        ],
    )
