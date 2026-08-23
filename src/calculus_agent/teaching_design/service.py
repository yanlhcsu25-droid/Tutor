"""Deterministic lifecycle service for TeachingDesign."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .models import TeachingDesignVersionRecord
from .persistence import TeachingDesignRepository
from .schemas import (
    TeachingDesignContent,
    TeachingDesignPatch,
    TeachingDesignRead,
)


class TeachingDesignNotFoundError(LookupError):
    pass


class TeachingDesignStateError(RuntimeError):
    pass


class StaleTeachingDesignError(TeachingDesignStateError):
    pass


def _serialize(record: TeachingDesignVersionRecord) -> TeachingDesignRead:
    return TeachingDesignRead(
        version_id=record.id,
        design_key=record.design_key,
        owner_key=record.owner_key,
        source_conversation_id=record.source_conversation_id,
        parent_version_id=record.parent_version_id,
        version=record.version,
        status=record.status,
        content=TeachingDesignContent.model_validate(record.design_json),
        created_by_run_id=record.created_by_run_id,
        source_user_message=record.source_user_message,
        change_reason=record.change_reason,
        created_at=record.created_at.isoformat(),
        confirmed_by_run_id=record.confirmed_by_run_id,
        confirmed_at=(
            record.confirmed_at.isoformat()
            if record.confirmed_at is not None
            else None
        ),
        superseded_by_version_id=record.superseded_by_version_id,
        superseded_at=(
            record.superseded_at.isoformat()
            if record.superseded_at is not None
            else None
        ),
    )


class TeachingDesignService:
    """Business source of truth for design creation/version/confirmation/recall.

    Invariants:
    - version content is immutable;
    - revisions are linear (no hidden branching);
    - active = most recently discussed version in a conversation;
    - effective confirmed = currently approved version of a design;
    - a newer unconfirmed version does not invalidate the confirmed version;
    - stale confirmation is rejected deterministically.
    """

    def __init__(
        self,
        session: Session,
        repository: TeachingDesignRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or TeachingDesignRepository(session)

    def _required(self, version_id: str) -> TeachingDesignVersionRecord:
        record = self.repository.get_version(version_id)
        if record is None:
            raise TeachingDesignNotFoundError(version_id)
        return record

    def _assert_latest(
        self,
        record: TeachingDesignVersionRecord,
    ) -> None:
        latest = self.repository.latest_version(record.design_key)
        if latest is None or latest.id != record.id:
            raise StaleTeachingDesignError("stale_teaching_design_version")

    def create(
        self,
        *,
        owner_key: str,
        conversation_id: str,
        content: TeachingDesignContent | dict,
        run_id: str | None,
        source_user_message: str | None,
        change_reason: str | None = None,
        ready_for_confirmation: bool = True,
    ) -> TeachingDesignRead:
        validated = TeachingDesignContent.model_validate(content)
        active = self.get_active(
            owner_key=owner_key, conversation_id=conversation_id,
        )
        if (
            active is not None
            and active.status in {"draft", "awaiting_confirmation"}
            and active.source_user_message == source_user_message
        ):
            # Replayed delivery of the same teacher request returns the existing
            # immutable artifact instead of creating a duplicate version.
            return active

        record = TeachingDesignVersionRecord(
            design_key=str(uuid.uuid4()),
            owner_key=owner_key,
            source_conversation_id=conversation_id,
            parent_version_id=None,
            version=1,
            status=(
                "awaiting_confirmation"
                if ready_for_confirmation
                else "draft"
            ),
            title=validated.title,
            design_json=validated.model_dump(mode="json"),
            created_by_run_id=run_id,
            source_user_message=source_user_message,
            change_reason=change_reason or "initial_design",
        )
        self.repository.add_version(record)
        self.repository.set_active(
            owner_key=owner_key,
            conversation_id=conversation_id,
            version_id=record.id,
            run_id=run_id,
        )
        return _serialize(record)

    def revise(
        self,
        version_id: str,
        patch: TeachingDesignPatch | dict,
        *,
        conversation_id: str,
        run_id: str | None,
        source_user_message: str | None,
        change_reason: str,
        ready_for_confirmation: bool = True,
    ) -> TeachingDesignRead:
        base = self._required(version_id)
        self._assert_latest(base)

        patch_model = TeachingDesignPatch.model_validate(patch)
        patch_values = patch_model.model_dump(
            exclude_unset=True,
            mode="json",
        )
        if not patch_values:
            raise TeachingDesignStateError("empty_teaching_design_patch")

        original = TeachingDesignContent.model_validate(base.design_json)
        revised = TeachingDesignContent.model_validate(
            {
                **original.model_dump(mode="json"),
                **patch_values,
            }
        )

        revision = TeachingDesignVersionRecord(
            design_key=base.design_key,
            owner_key=base.owner_key,
            source_conversation_id=conversation_id,
            parent_version_id=base.id,
            version=self.repository.next_version_number(base.design_key),
            status=(
                "awaiting_confirmation"
                if ready_for_confirmation
                else "draft"
            ),
            title=revised.title,
            design_json=revised.model_dump(mode="json"),
            created_by_run_id=run_id,
            source_user_message=source_user_message,
            change_reason=change_reason,
        )
        self.repository.add_version(revision)

        # Only unconfirmed work is superseded immediately. A confirmed version
        # remains effective until the teacher confirms a replacement.
        if base.status in {"draft", "awaiting_confirmation"}:
            base.status = "superseded"
            base.superseded_by_version_id = revision.id
            base.superseded_at = datetime.now(UTC)
            self.session.flush()

        self.repository.set_active(
            owner_key=revision.owner_key,
            conversation_id=conversation_id,
            version_id=revision.id,
            run_id=run_id,
        )
        return _serialize(revision)

    def submit_for_confirmation(
        self,
        version_id: str,
        *,
        conversation_id: str,
        run_id: str | None,
    ) -> TeachingDesignRead:
        record = self._required(version_id)
        self._assert_latest(record)
        if record.status != "draft":
            raise TeachingDesignStateError(
                f"cannot_submit_from_status:{record.status}"
            )
        record.status = "awaiting_confirmation"
        self.repository.set_active(
            owner_key=record.owner_key,
            conversation_id=conversation_id,
            version_id=record.id,
            run_id=run_id,
        )
        self.session.flush()
        return _serialize(record)

    def confirm(
        self,
        version_id: str,
        *,
        conversation_id: str,
        run_id: str | None,
    ) -> TeachingDesignRead:
        record = self._required(version_id)
        self._assert_latest(record)
        if record.status != "awaiting_confirmation":
            raise TeachingDesignStateError(
                f"cannot_confirm_from_status:{record.status}"
            )

        previous = self.repository.effective_confirmed(record.design_key)
        now = datetime.now(UTC)

        if previous is not None and previous.id != record.id:
            previous.status = "superseded"
            previous.superseded_by_version_id = record.id
            previous.superseded_at = now

        record.status = "confirmed"
        record.confirmed_by_run_id = run_id
        record.confirmed_at = now

        self.repository.set_active(
            owner_key=record.owner_key,
            conversation_id=conversation_id,
            version_id=record.id,
            run_id=run_id,
        )
        self.session.flush()
        return _serialize(record)

    def discard_unconfirmed(
        self,
        version_id: str,
        *,
        conversation_id: str,
        run_id: str | None,
    ) -> TeachingDesignRead:
        """Abandon an unconfirmed design and remove its active pointer."""
        record = self._required(version_id)
        self._assert_latest(record)
        if record.status not in {"draft", "awaiting_confirmation"}:
            raise TeachingDesignStateError(
                f"cannot_discard_from_status:{record.status}"
            )
        record.status = "superseded"
        record.superseded_at = datetime.now(UTC)
        self.repository.clear_active(
            owner_key=record.owner_key,
            conversation_id=conversation_id,
        )
        self.session.flush()
        return _serialize(record)

    def activate_historical_version(
        self,
        version_id: str,
        *,
        owner_key: str,
        conversation_id: str,
        run_id: str | None,
    ) -> TeachingDesignRead:
        """Recall a previous design without changing any business status."""
        record = self._required(version_id)
        if record.owner_key != owner_key:
            raise TeachingDesignNotFoundError(version_id)
        self.repository.set_active(
            owner_key=owner_key,
            conversation_id=conversation_id,
            version_id=record.id,
            run_id=run_id,
        )
        return _serialize(record)

    def get(self, version_id: str) -> TeachingDesignRead:
        return _serialize(self._required(version_id))

    def get_active(
        self,
        *,
        owner_key: str,
        conversation_id: str,
    ) -> TeachingDesignRead | None:
        record = self.repository.get_active(
            owner_key=owner_key,
            conversation_id=conversation_id,
        )
        return _serialize(record) if record is not None else None

    def get_effective_confirmed(
        self,
        *,
        design_key: str,
    ) -> TeachingDesignRead | None:
        record = self.repository.effective_confirmed(design_key)
        return _serialize(record) if record is not None else None

    def recall_candidates(
        self,
        *,
        owner_key: str,
        query: str,
        limit: int = 10,
    ) -> list[TeachingDesignRead]:
        """Deterministic candidate retrieval for long-term design memory.

        Agent semantic resolution happens above this service. This method does
        not use chat history as business truth.
        """
        needle = query.strip().lower()

        # Only expose the latest version of each design in recall candidates.
        latest_by_key: dict[str, TeachingDesignVersionRecord] = {}
        for record in self.repository.list_owner_versions(
            owner_key=owner_key,
            limit=300,
        ):
            current = latest_by_key.get(record.design_key)
            if current is None or record.version > current.version:
                latest_by_key[record.design_key] = record

        values = sorted(
            latest_by_key.values(),
            key=lambda item: (item.created_at, item.version),
            reverse=True,
        )

        if not needle:
            return [_serialize(item) for item in values[:limit]]

        result: list[TeachingDesignRead] = []
        for record in values:
            content = TeachingDesignContent.model_validate(record.design_json)
            haystack = " ".join(
                [
                    content.title,
                    content.objective,
                    *content.scope_names,
                    *content.teaching_priorities,
                ]
            ).lower()
            if needle in haystack:
                result.append(_serialize(record))
                if len(result) >= limit:
                    break
        return result
