"""Structured bridge to the existing Paper workflow version operations."""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.agent.version_parser import VersionOperationIntent
from calculus_agent.models import Paper
from calculus_agent.papers.workflow import (
    BlueprintStateError,
    WorkflowNotFoundError,
    redo_paper_operation,
    restore_paper_version,
    undo_paper_operations,
)


class VersionOperationResult(BaseModel):
    ok: bool
    action: Literal["undo", "redo", "restore"]
    paper_id: str | None = None
    previous_version_id: str | None = None
    current_version_id: str | None = None
    target_version: int | None = None
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)


def run_version_operation(
    session: Session, *, paper_id: str, version_id: str, intent: VersionOperationIntent
) -> VersionOperationResult:
    base = dict(action=intent.action, paper_id=paper_id, previous_version_id=version_id, target_version=intent.target_version)
    paper = session.get(Paper, paper_id)
    current = session.get(Paper, version_id)
    if paper is None:
        return VersionOperationResult(ok=False, blocking_errors=["paper_not_found"], **base)
    if current is None:
        return VersionOperationResult(ok=False, blocking_errors=["version_not_found"], **base)
    if (paper.root_paper_id or paper.id) != (current.root_paper_id or current.id):
        return VersionOperationResult(ok=False, blocking_errors=["paper_version_mismatch"], **base)
    try:
        if intent.action == "undo":
            result = undo_paper_operations(session, version_id)
        elif intent.action == "redo":
            result = redo_paper_operation(session, version_id)
        else:
            target = session.scalar(select(Paper).where(
                Paper.root_paper_id == (current.root_paper_id or current.id),
                Paper.version == intent.target_version,
            ))
            if target is None:
                return VersionOperationResult(ok=False, blocking_errors=["version_not_found"], **base)
            result = restore_paper_version(session, version_id, target.id)
    except WorkflowNotFoundError:
        return VersionOperationResult(ok=False, blocking_errors=["version_not_found"], **base)
    except BlueprintStateError:
        code = "nothing_to_undo" if intent.action == "undo" else "nothing_to_redo" if intent.action == "redo" else "version_operation_failed"
        return VersionOperationResult(ok=False, blocking_errors=[code], **base)
    return VersionOperationResult(ok=True, current_version_id=result.paper_id, **base)
