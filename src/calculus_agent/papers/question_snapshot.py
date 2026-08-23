"""Immutable Question content captured when a Paper item is created."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from calculus_agent.models import Question, QuestionDraft


def capture_question_snapshot(session: Session, question_id: str) -> dict[str, Any]:
    question = session.get(Question, question_id)
    if question is None:
        raise ValueError(f"question_not_found:{question_id}")
    draft = session.get(QuestionDraft, question.draft_id)
    return {
        "question_text": question.question_text,
        "question_type": question.question_type,
        "final_answer": question.final_answer,
        "solution_json": dict(question.solution_json or {}),
        "options": list((draft.options_json if draft is not None else None) or []),
    }
