"""Read-only access to the concrete paper version in the current Agent context."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    KnowledgeNode,
    Paper,
    PaperItem,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from calculus_agent.question_types import canonical_question_type


class ReadCurrentPaperInput(BaseModel):
    positions: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None,
        description=(
            "Exact 1-based question positions to read. When the teacher names one or "
            "more question numbers, pass exactly those numbers; for example, 第五题 "
            "requires positions=[5]. Use null only for a whole-paper overview."
        ),
    )


class CurrentPaperSummary(BaseModel):
    paper_id: str
    version: int
    title: str
    question_count: int
    total_score: float


class CurrentPaperQuestion(BaseModel):
    position: int
    question_id: str
    question_type: str
    score: float
    difficulty: int | None = None
    knowledge_points: list[str] = Field(default_factory=list)
    preview: str | None = None
    content: str | None = None
    options: list[str] = Field(default_factory=list)


class ReadCurrentPaperResult(BaseModel):
    ok: bool
    status: Literal["ok", "failed"]
    code: str | None = None
    message: str | None = None
    paper: CurrentPaperSummary | None = None
    questions: list[CurrentPaperQuestion] = Field(default_factory=list)
    position: int | None = None
    question_count: int | None = None


def _latest_difficulties(session: Session, question_ids: list[str]) -> dict[str, int]:
    latest = (
        select(
            QuestionProfile.question_id,
            func.max(QuestionProfile.profile_version).label("profile_version"),
        )
        .where(
            QuestionProfile.question_id.in_(question_ids),
            QuestionProfile.profile_status == "approved",
        )
        .group_by(QuestionProfile.question_id)
        .subquery()
    )
    return dict(session.execute(
        select(QuestionProfile.question_id, QuestionProfile.difficulty).join(
            latest,
            (QuestionProfile.question_id == latest.c.question_id)
            & (QuestionProfile.profile_version == latest.c.profile_version),
        )
    ).all())


def _knowledge_names(session: Session, question_ids: list[str]) -> dict[str, list[str]]:
    result = {question_id: [] for question_id in question_ids}
    rows = session.execute(
        select(QuestionKnowledgeLink.question_id, KnowledgeNode.name)
        .join(KnowledgeNode, KnowledgeNode.id == QuestionKnowledgeLink.knowledge_node_id)
        .where(QuestionKnowledgeLink.question_id.in_(question_ids))
        .order_by(QuestionKnowledgeLink.question_id, KnowledgeNode.name)
    )
    for question_id, name in rows:
        if name not in result[question_id]:
            result[question_id].append(name)
    return result


def _preview(content: str, *, limit: int = 120) -> str:
    normalized = " ".join(content.split())
    return normalized if len(normalized) <= limit else normalized[:limit].rstrip() + "……"


def read_current_paper(
    session: Session,
    *,
    current_paper_version_id: str | None,
    request: ReadCurrentPaperInput,
) -> ReadCurrentPaperResult:
    """Read one concrete Paper.id without creating or changing any state."""
    if not current_paper_version_id:
        return ReadCurrentPaperResult(
            ok=False,
            status="failed",
            code="no_current_paper",
            message="当前还没有可查看的试卷。",
        )
    paper = session.get(Paper, current_paper_version_id)
    if paper is None:
        return ReadCurrentPaperResult(
            ok=False,
            status="failed",
            code="no_current_paper",
            message="当前还没有可查看的试卷。",
        )

    all_items = list(session.scalars(
        select(PaperItem)
        .where(PaperItem.paper_id == paper.id)
        .order_by(PaperItem.position)
    ))
    by_position = {item.position: item for item in all_items}
    requested_positions = list(dict.fromkeys(request.positions or []))
    missing = next((position for position in requested_positions if position not in by_position), None)
    if missing is not None:
        return ReadCurrentPaperResult(
            ok=False,
            status="failed",
            code="question_position_not_found",
            message=f"当前试卷没有第{missing}题。",
            position=missing,
            question_count=len(all_items),
        )

    selected = [by_position[position] for position in requested_positions] if requested_positions else all_items
    question_ids = [item.question_id for item in selected]
    questions = {
        question.id: question
        for question in session.scalars(select(Question).where(Question.id.in_(question_ids)))
    }
    drafts = {
        draft.id: draft
        for draft in session.scalars(select(QuestionDraft).where(
            QuestionDraft.id.in_([question.draft_id for question in questions.values()])
        ))
    }
    difficulties = _latest_difficulties(session, question_ids)
    knowledge = _knowledge_names(session, question_ids)
    overview = not requested_positions
    result_questions = []
    for item in selected:
        question = questions[item.question_id]
        draft = drafts.get(question.draft_id)
        result_questions.append(CurrentPaperQuestion(
            position=item.position,
            question_id=question.id,
            question_type=canonical_question_type(item.section or question.question_type),
            score=item.score,
            difficulty=difficulties.get(question.id),
            knowledge_points=knowledge.get(question.id, []),
            preview=_preview(question.question_text) if overview else None,
            content=None if overview else question.question_text,
            options=[] if overview or draft is None else list(draft.options_json or []),
        ))

    return ReadCurrentPaperResult(
        ok=True,
        status="ok",
        paper=CurrentPaperSummary(
            paper_id=paper.id,
            version=paper.version,
            title=paper.title,
            question_count=len(all_items),
            total_score=sum(item.score for item in all_items),
        ),
        questions=result_questions,
        question_count=len(all_items),
    )
