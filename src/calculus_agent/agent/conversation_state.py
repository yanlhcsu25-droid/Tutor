"""Minimal persisted state and short conversational context for Teacher Agent."""

from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    AgentPendingAdjustment,
    AgentPendingGeneration,
    AgentPendingReplacement,
    AgentWorkingMemoryRecord,
    TeacherAgentConversationMessage,
)

from .schemas import AgentWorkingMemory, GeneratePaperInput


class PendingGeneration(BaseModel):
    request: GeneratePaperInput
    total_score_source: Literal[
        "teacher_explicit",
        "teaching_design",
        "pending_inherited",
        "default_template",
        "system_rebalanced",
    ] = "default_template"
    locked_score_question_types: list[str] = Field(default_factory=list)
    teaching_design_version_id: str | None = None
    pending_version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def derive_question_count(self) -> "PendingGeneration":
        requirements = self.request.question_type_requirements or []
        if requirements:
            provenance = dict(self.request.constraint_provenance)
            if "question_count" in provenance:
                item = provenance["question_count"]
                if isinstance(item, dict):
                    item = {
                        **item,
                        "merge_location": (
                            f"{item.get('merge_location')};"
                            "PendingGeneration.derive_question_count"
                        ),
                    }
                else:
                    item = item.model_copy(update={
                        "merge_location": (
                            f"{item.merge_location};"
                            "PendingGeneration.derive_question_count"
                        ),
                    })
                provenance["question_count"] = item
            self.request = self.request.model_copy(update={
                "question_count": sum(item.count for item in requirements),
                "constraint_provenance": provenance,
            })
        return self


class PendingGenerationStaleError(RuntimeError):
    """Raised when an older confirmation-card revision tries to overwrite a plan."""


class PendingReplacement(BaseModel):
    """Persisted confirmation contract for one single-question replacement.

    ``required_knowledge_node_ids`` is a preview-time snapshot.  When the
    teacher explicitly asks to preserve knowledge points, confirmation must
    revalidate that snapshot against current database state before mutation.
    """

    action: Literal["replace_question"] = "replace_question"
    paper_id: str
    source_version_id: str
    target_position: int = Field(gt=0)
    old_question_id: str
    replacement_question_id: str
    difficulty_direction: Literal["easier", "harder", "same"] | None = None
    target_difficulty: int | None = Field(default=None, ge=1, le=5)
    preserve_knowledge_points: bool = False
    required_knowledge_node_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PendingReplacementStore(Protocol):
    def get(self, conversation_id: str) -> PendingReplacement | None: ...
    def set(self, conversation_id: str, action: PendingReplacement) -> None: ...
    def clear(self, conversation_id: str) -> None: ...


class DatabasePendingReplacementStore:
    """Session-backed store; isolation is enforced by conversation_id primary key."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, conversation_id: str) -> PendingReplacement | None:
        record = self.session.get(AgentPendingReplacement, conversation_id)
        return PendingReplacement.model_validate(record.payload_json) if record else None

    def set(self, conversation_id: str, action: PendingReplacement) -> None:
        record = self.session.get(AgentPendingReplacement, conversation_id)
        if record is None:
            self.session.add(AgentPendingReplacement(
                conversation_id=conversation_id, payload_json=action.model_dump(mode="json")
            ))
        else:
            record.payload_json = action.model_dump(mode="json")
        self.session.flush()

    def clear(self, conversation_id: str) -> None:
        record = self.session.get(AgentPendingReplacement, conversation_id)
        if record is not None:
            self.session.delete(record)
            self.session.flush()

    def get_adjustment(self, conversation_id: str) -> str | None:
        record = self.session.get(AgentPendingAdjustment, conversation_id)
        return record.plan_id if record else None

    def set_adjustment(self, conversation_id: str, plan_id: str) -> None:
        record = self.session.get(AgentPendingAdjustment, conversation_id)
        if record is None:
            self.session.add(AgentPendingAdjustment(conversation_id=conversation_id, plan_id=plan_id))
        else:
            record.plan_id = plan_id
        self.session.flush()

    def clear_adjustment(self, conversation_id: str) -> None:
        record = self.session.get(AgentPendingAdjustment, conversation_id)
        if record is not None:
            self.session.delete(record)
            self.session.flush()

    def get_generation(self, conversation_id: str) -> PendingGeneration | None:
        record = self.session.get(AgentPendingGeneration, conversation_id)
        if record is None:
            return None
        return PendingGeneration.model_validate(record.payload_json)

    def set_generation(
        self,
        conversation_id: str,
        pending: PendingGeneration,
        *,
        expected_version: int | None = None,
    ) -> PendingGeneration:
        record = self.session.get(AgentPendingGeneration, conversation_id)
        if record is None:
            if expected_version not in (None, 0):
                raise PendingGenerationStaleError("stale_pending_plan")
            stored = pending.model_copy(update={"pending_version": 1})
            self.session.add(AgentPendingGeneration(
                conversation_id=conversation_id,
                payload_json=stored.model_dump(mode="json"),
            ))
        else:
            current = PendingGeneration.model_validate(record.payload_json)
            if expected_version is not None and current.pending_version != expected_version:
                raise PendingGenerationStaleError("stale_pending_plan")
            stored = pending.model_copy(update={"pending_version": current.pending_version + 1})
            record.payload_json = stored.model_dump(mode="json")
        self.session.flush()
        return stored

    def clear_generation(self, conversation_id: str) -> None:
        record = self.session.get(AgentPendingGeneration, conversation_id)
        if record is not None:
            self.session.delete(record)
            self.session.flush()

    def get_memory(self, conversation_id: str) -> AgentWorkingMemory:
        record = self.session.get(AgentWorkingMemoryRecord, conversation_id)
        return AgentWorkingMemory.model_validate(record.payload_json) if record else AgentWorkingMemory()

    def set_memory(self, conversation_id: str, memory: AgentWorkingMemory) -> None:
        record = self.session.get(AgentWorkingMemoryRecord, conversation_id)
        if record is None:
            self.session.add(AgentWorkingMemoryRecord(conversation_id=conversation_id, payload_json=memory.model_dump(mode="json")))
        else:
            record.payload_json = memory.model_dump(mode="json")
        self.session.flush()


class DatabaseConversationHistoryStore:
    """Stores only a small recent user/assistant transcript per conversation."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def recent_messages(self, conversation_id: str, *, limit: int = 6) -> list[dict[str, str]]:
        rows = list(self.session.scalars(
            select(TeacherAgentConversationMessage)
            .where(TeacherAgentConversationMessage.conversation_id == conversation_id)
            .order_by(TeacherAgentConversationMessage.created_at.desc(), TeacherAgentConversationMessage.id.desc())
            .limit(limit)
        ))
        return [
            {"role": row.role, "content": row.content}
            for row in reversed(rows)
        ]

    def list_messages(self, conversation_id: str, *, limit: int = 500) -> list[dict[str, str]]:
        """Return persisted UI history without changing the Agent's short context window."""
        rows = list(self.session.scalars(
            select(TeacherAgentConversationMessage)
            .where(TeacherAgentConversationMessage.conversation_id == conversation_id)
            .order_by(TeacherAgentConversationMessage.created_at, TeacherAgentConversationMessage.id)
            .limit(limit)
        ))
        return [{"role": row.role, "content": row.content} for row in rows]

    def append(self, conversation_id: str, *, role: Literal["user", "assistant"], content: str) -> None:
        self.session.add(TeacherAgentConversationMessage(
            conversation_id=conversation_id, role=role, content=content
        ))
        self.session.flush()
