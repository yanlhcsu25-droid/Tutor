from __future__ import annotations

import os
from pathlib import Path

import pytest


INTEGRATION_DB_ENV = "CALCULUS_AGENT_SCOPE_TEST_DB"


def configured_integration_db_path() -> Path:
    """Return the explicitly configured local integration-test database.

    Local production-like DB tests must never auto-discover a repository
    ``calculus_agent.db``. Their execution is opt-in through
    ``CALCULUS_AGENT_SCOPE_TEST_DB`` so ordinary unit/full-suite runs remain
    isolated from accidental worktree files.
    """
    raw_path = os.environ.get(INTEGRATION_DB_ENV)
    if not raw_path:
        pytest.skip(
            "local DB integration test requires "
            "CALCULUS_AGENT_SCOPE_TEST_DB"
        )

    db_path = Path(raw_path).expanduser()
    if not db_path.is_absolute():
        db_path = db_path.resolve()

    if not db_path.is_file():
        pytest.fail(
            f"{INTEGRATION_DB_ENV} points to a missing database: {db_path}"
        )

    return db_path
