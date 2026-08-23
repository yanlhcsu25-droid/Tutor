"""Persistence boundary for Agent-created draft papers."""

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from calculus_agent.models import Paper, PaperBlueprintRecord, PaperItem
from calculus_agent.schemas import PaperBlueprint, PaperPreviewRead
from calculus_agent.agent.schemas import GenerationConstraints
from calculus_agent.papers.question_snapshot import capture_question_snapshot


class DraftPaperResult(BaseModel):
    ok: bool
    paper_id: str | None = None
    version_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)


def create_paper_draft(
    session: Session,
    composed_paper: PaperPreviewRead,
    blueprint: PaperBlueprint,
    *,
    source: str = "teacher_agent",
    generation_constraints: GenerationConstraints | None = None,
) -> DraftPaperResult:
    """Persist one generated Paper without owning the caller transaction."""
    if not composed_paper.feasible:
        return DraftPaperResult(
            ok=False,
            blocking_errors=["paper_not_feasible"],
        )

    try:
        with session.begin_nested():
            blueprint_payload = blueprint.model_dump(mode="json")
            metadata = {"source": source}
            if generation_constraints is not None:
                metadata.update(
                    generation_constraints.model_dump(mode="json")
                )
                metadata["difficulty"] = (
                    generation_constraints.allowed_difficulty_levels
                )
            blueprint_payload["_agent_metadata"] = metadata

            record = PaperBlueprintRecord(
                title=blueprint.title,
                blueprint_json=blueprint_payload,
                status="draft",
            )
            session.add(record)
            session.flush()

            paper = Paper(
                blueprint_id=record.id,
                version=1,
                status="draft",
                title=composed_paper.title,
                total_score=round(composed_paper.total_score),
                validation_status="pending",
            )
            session.add(paper)
            session.flush()
            paper.root_paper_id = paper.id

            for position, item in enumerate(composed_paper.items, 1):
                session.add(
                    PaperItem(
                        paper_id=paper.id,
                        question_id=item.question_id,
                        section=item.question_type,
                        position=position,
                        score=item.score,
                        locked=item.locked,
                        question_snapshot_json=capture_question_snapshot(
                            session, item.question_id
                        ),
                    )
                )
            session.flush()

        return DraftPaperResult(
            ok=True,
            paper_id=paper.id,
            version_id=paper.id,
        )
    except Exception as exc:
        return DraftPaperResult(
            ok=False,
            blocking_errors=[
                "paper_persistence_failed",
                str(exc),
            ],
        )
