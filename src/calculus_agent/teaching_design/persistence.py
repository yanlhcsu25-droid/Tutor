"""Persistence boundary for TeachingDesign.

No Agent runtime/tool code belongs here. The repository only reads/writes
TeachingDesign tables and returns ORM records to the application service.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ActiveTeachingDesignRecord, TeachingDesignVersionRecord


class TeachingDesignRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_version(self, version_id: str) -> TeachingDesignVersionRecord | None:
        return self.session.get(TeachingDesignVersionRecord, version_id)

    def latest_version(
        self,
        design_key: str,
    ) -> TeachingDesignVersionRecord | None:
        return self.session.scalar(
            select(TeachingDesignVersionRecord)
            .where(TeachingDesignVersionRecord.design_key == design_key)
            .order_by(TeachingDesignVersionRecord.version.desc())
            .limit(1)
        )

    def next_version_number(self, design_key: str) -> int:
        value = self.session.scalar(
            select(func.max(TeachingDesignVersionRecord.version))
            .where(TeachingDesignVersionRecord.design_key == design_key)
        )
        return int(value or 0) + 1

    def effective_confirmed(
        self,
        design_key: str,
    ) -> TeachingDesignVersionRecord | None:
        return self.session.scalar(
            select(TeachingDesignVersionRecord)
            .where(
                TeachingDesignVersionRecord.design_key == design_key,
                TeachingDesignVersionRecord.status == "confirmed",
            )
            .order_by(TeachingDesignVersionRecord.version.desc())
            .limit(1)
        )

    def add_version(self, record: TeachingDesignVersionRecord) -> None:
        self.session.add(record)
        self.session.flush()

    def set_active(
        self,
        *,
        owner_key: str,
        conversation_id: str,
        version_id: str,
        run_id: str | None,
    ) -> None:
        key = (owner_key, conversation_id)
        active = self.session.get(ActiveTeachingDesignRecord, key)
        now = datetime.now(UTC)
        if active is None:
            active = ActiveTeachingDesignRecord(
                owner_key=owner_key,
                conversation_id=conversation_id,
                design_version_id=version_id,
                activated_by_run_id=run_id,
                updated_at=now,
            )
            self.session.add(active)
        else:
            active.design_version_id = version_id
            active.activated_by_run_id = run_id
            active.updated_at = now
        self.session.flush()

    def get_active(
        self,
        *,
        owner_key: str,
        conversation_id: str,
    ) -> TeachingDesignVersionRecord | None:
        active = self.session.get(
            ActiveTeachingDesignRecord,
            (owner_key, conversation_id),
        )
        if active is None:
            return None
        return self.get_version(active.design_version_id)

    def list_owner_versions(
        self,
        *,
        owner_key: str,
        limit: int = 200,
    ) -> list[TeachingDesignVersionRecord]:
        return list(
            self.session.scalars(
                select(TeachingDesignVersionRecord)
                .where(TeachingDesignVersionRecord.owner_key == owner_key)
                .order_by(
                    TeachingDesignVersionRecord.created_at.desc(),
                    TeachingDesignVersionRecord.version.desc(),
                )
                .limit(limit)
            ).all()
        )
