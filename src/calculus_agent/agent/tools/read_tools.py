"""Read-only access to the concrete paper version in the current Agent context."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
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
from calculus_agent.papers.addressing import (
    QuestionAddress,
    resolve_section_item_from_items,
    section_order_map,
)
from calculus_agent.question_types import canonical_question_type


class ReadCurrentPaperInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None,
        description=(
            "Legacy internal global positions. Keep for backward compatibility. "
            "Do not infer these from normal teacher-facing numbering."
        ),
    )
    addresses: list[QuestionAddress] | None = Field(
        default=None,
        description=(
            "Teacher-facing section-local addresses. Example: 填空题第2题 -> "
            '[{"section_type":"填空题","section_order":2}].'
        ),
    )

    @model_validator(mode="after")
    def addressing_is_unambiguous(self):
        if self.positions and self.addresses:
            raise ValueError("positions 和 addresses 不能同时使用")
        return self


class CurrentPaperSummary(BaseModel):
    paper_id: str
    version: int
    title: str
    question_count: int
    total_score: float


class CurrentPaperQuestion(BaseModel):
    item_id: str
    position: int = Field(
        description="Internal global order; not the teacher-facing number."
    )
    section_order: int
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
    section_type: str | None = None
    section_order: int | None = None
    question_count: int | None = None


def _latest_difficulties(
    session: Session,
    question_ids: list[str],
) -> dict[str, int]:
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
    return dict(
        session.execute(
            select(
                QuestionProfile.question_id,
                QuestionProfile.difficulty,
            ).join(
                latest,
                (QuestionProfile.question_id == latest.c.question_id)
                & (
                    QuestionProfile.profile_version
                    == latest.c.profile_version
                ),
            )
        ).all()
    )


def _knowledge_names(
    session: Session,
    question_ids: list[str],
) -> dict[str, list[str]]:
    result = {
        question_id: []
        for question_id in question_ids
    }
    rows = session.execute(
        select(
            QuestionKnowledgeLink.question_id,
            KnowledgeNode.name,
        )
        .join(
            KnowledgeNode,
            KnowledgeNode.id
            == QuestionKnowledgeLink.knowledge_node_id,
        )
        .where(
            QuestionKnowledgeLink.question_id.in_(
                question_ids
            )
        )
        .order_by(
            QuestionKnowledgeLink.question_id,
            KnowledgeNode.name,
        )
    )
    for question_id, name in rows:
        if name not in result[question_id]:
            result[question_id].append(name)

    return result


def _preview(
    content: str,
    *,
    limit: int = 120,
) -> str:
    normalized = " ".join(content.split())
    return (
        normalized
        if len(normalized) <= limit
        else normalized[:limit].rstrip() + "……"
    )


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

    paper = session.get(
        Paper,
        current_paper_version_id,
    )
    if paper is None:
        return ReadCurrentPaperResult(
            ok=False,
            status="failed",
            code="no_current_paper",
            message="当前还没有可查看的试卷。",
        )

    all_items = list(
        session.scalars(
            select(PaperItem)
            .where(PaperItem.paper_id == paper.id)
            .order_by(PaperItem.position)
        )
    )

    by_position = {
        item.position: item
        for item in all_items
    }
    section_orders = section_order_map(all_items)

    requested_positions = list(
        dict.fromkeys(request.positions or [])
    )
    requested_addresses = list(
        request.addresses or []
    )

    selected: list[PaperItem]

    if requested_addresses:
        selected = []
        seen_item_ids: set[str] = set()

        for address in requested_addresses:
            item = resolve_section_item_from_items(
                all_items,
                section_type=address.section_type,
                section_order=address.section_order,
            )

            if item is None:
                return ReadCurrentPaperResult(
                    ok=False,
                    status="failed",
                    code="question_address_not_found",
                    message=(
                        f"当前试卷没有"
                        f"{address.section_type}"
                        f"第{address.section_order}题。"
                    ),
                    section_type=address.section_type,
                    section_order=address.section_order,
                    question_count=len(all_items),
                )

            if item.id not in seen_item_ids:
                selected.append(item)
                seen_item_ids.add(item.id)

    elif requested_positions:
        missing = next(
            (
                position
                for position in requested_positions
                if position not in by_position
            ),
            None,
        )

        if missing is not None:
            return ReadCurrentPaperResult(
                ok=False,
                status="failed",
                code="question_position_not_found",
                message=f"当前试卷没有内部全局第{missing}题。",
                position=missing,
                question_count=len(all_items),
            )

        selected = [
            by_position[position]
            for position in requested_positions
        ]

    else:
        selected = all_items

    question_ids = [
        item.question_id
        for item in selected
    ]

    questions = {
        question.id: question
        for question in session.scalars(
            select(Question).where(
                Question.id.in_(question_ids)
            )
        )
    }

    drafts = {
        draft.id: draft
        for draft in session.scalars(
            select(QuestionDraft).where(
                QuestionDraft.id.in_(
                    [
                        question.draft_id
                        for question in questions.values()
                    ]
                )
            )
        )
    }

    difficulties = _latest_difficulties(
        session,
        question_ids,
    )
    knowledge = _knowledge_names(
        session,
        question_ids,
    )

    overview = (
        not requested_positions
        and not requested_addresses
    )

    result_questions = []

    for item in selected:
        question = questions[item.question_id]
        draft = drafts.get(question.draft_id)
        snapshot = item.question_snapshot_json or {}
        question_text = snapshot.get("question_text", question.question_text)

        result_questions.append(
            CurrentPaperQuestion(
                item_id=item.id,
                position=item.position,
                section_order=section_orders[item.id],
                question_id=question.id,
                question_type=canonical_question_type(
                    item.section
                    or question.question_type
                ),
                score=item.score,
                difficulty=difficulties.get(
                    question.id
                ),
                knowledge_points=knowledge.get(
                    question.id,
                    [],
                ),
                preview=(
                    _preview(question_text)
                    if overview
                    else None
                ),
                content=(
                    None
                    if overview
                    else question_text
                ),
                options=(
                    []
                    if overview or draft is None
                    else list(
                        snapshot.get("options", draft.options_json) or []
                    )
                ),
            )
        )

    return ReadCurrentPaperResult(
        ok=True,
        status="ok",
        paper=CurrentPaperSummary(
            paper_id=paper.id,
            version=paper.version,
            title=paper.title,
            question_count=len(all_items),
            total_score=sum(
                item.score
                for item in all_items
            ),
        ),
        questions=result_questions,
        question_count=len(all_items),
    )
