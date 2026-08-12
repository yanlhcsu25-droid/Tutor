import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    KnowledgeNode,
    MistakePrepTask,
    Question,
    QuestionKnowledgeLink,
)
from calculus_agent.schemas import MistakePrepCreate, MistakePrepMatchRead, MistakePrepRead


def create_mistake_prep(session: Session, request: MistakePrepCreate) -> MistakePrepRead:
    matches = _match_questions(session, request)
    task = MistakePrepTask(
        grade=None,
        question_text=request.question_text,
        final_answer=request.final_answer,
        solution_text=request.solution_text,
        error_reason=request.error_reason,
        question_type=request.question_type,
        knowledge_names_json=request.knowledge_names,
        matched_question_ids_json=[item.question_id for item in matches],
    )
    session.add(task)
    session.flush()
    return _read(task, matches)


def get_mistake_prep(session: Session, task_id: str) -> MistakePrepRead | None:
    task = session.get(MistakePrepTask, task_id)
    if task is None:
        return None
    questions = list(
        session.scalars(
            select(Question).where(Question.id.in_(task.matched_question_ids_json))
        ).all()
    )
    by_id = {question.id: question for question in questions}
    matches = [
        _match_read(session, by_id[question_id], task.knowledge_names_json, task.question_type)
        for question_id in task.matched_question_ids_json
        if question_id in by_id
    ]
    return _read(task, matches)


def _match_questions(
    session: Session, request: MistakePrepCreate
) -> list[MistakePrepMatchRead]:
    statement = select(Question).where(
        Question.review_status == "approved",
        Question.is_active.is_(True),
        Question.knowledge_match_status == "current",
    )
    questions = list(session.scalars(statement).all())
    source_fingerprint = _fingerprint(request.question_text)
    ranked: list[tuple[float, Question, list[str]]] = []
    for question in questions:
        if _fingerprint(question.question_text) == source_fingerprint:
            continue
        knowledge = _knowledge_names(session, question.id)
        overlap = sorted(set(request.knowledge_names) & set(knowledge))
        if not overlap:
            continue
        reasons = [f"共同知识点：{'、'.join(overlap)}"]
        score = 4 * len(overlap)
        if request.question_type and question.question_type == request.question_type:
            score += 1.5
            reasons.append(f"题型相同：{question.question_type}")
        ranked.append((score, question, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return [
        _match_read(
            session,
            question,
            request.knowledge_names,
            request.question_type,
            reasons=reasons,
        )
        for _, question, reasons in ranked[: request.match_count]
    ]


def _match_read(
    session: Session,
    question: Question,
    target_knowledge: list[str],
    question_type: str | None,
    *,
    reasons: list[str] | None = None,
) -> MistakePrepMatchRead:
    knowledge = _knowledge_names(session, question.id)
    if reasons is None:
        overlap = sorted(set(target_knowledge) & set(knowledge))
        reasons = [f"共同知识点：{'、'.join(overlap)}"] if overlap else []
        if question_type and question.question_type == question_type:
            reasons.append(f"题型相同：{question.question_type}")
    steps = question.solution_json.get("solution_steps", []) if question.solution_json else []
    return MistakePrepMatchRead(
        question_id=question.id,
        question_text=question.question_text,
        question_type=question.question_type,
        final_answer=question.final_answer,
        solution_steps=[str(step) for step in steps],
        knowledge=knowledge,
        match_reasons=reasons,
    )


def _knowledge_names(session: Session, question_id: str) -> list[str]:
    return list(
        session.scalars(
            select(KnowledgeNode.name)
            .join(
                QuestionKnowledgeLink,
                QuestionKnowledgeLink.knowledge_node_id == KnowledgeNode.id,
            )
            .where(QuestionKnowledgeLink.question_id == question_id)
            .order_by(KnowledgeNode.name)
        ).all()
    )


def _fingerprint(value: str) -> str:
    return re.sub(r"\s+|[，。；：、,.!?？！]", "", value).lower()


def _read(task: MistakePrepTask, matches: list[MistakePrepMatchRead]) -> MistakePrepRead:
    return MistakePrepRead(
        id=task.id,
        question_text=task.question_text,
        final_answer=task.final_answer,
        solution_text=task.solution_text,
        error_reason=task.error_reason,
        question_type=task.question_type,
        knowledge_names=task.knowledge_names_json,
        matches=matches,
        created_at=task.created_at,
    )
