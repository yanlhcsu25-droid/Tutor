from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration_db import (
    INTEGRATION_DB_ENV,
    configured_integration_db_path,
)


def test_unconfigured_integration_db_skips_even_if_random_repo_db_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A coincidental calculus_agent.db must never activate local DB tests."""
    (tmp_path / "calculus_agent.db").write_bytes(b"not-a-real-production-db")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(INTEGRATION_DB_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        configured_integration_db_path()


def test_configured_missing_integration_db_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    missing = tmp_path / "missing.db"
    monkeypatch.setenv(INTEGRATION_DB_ENV, str(missing))

    with pytest.raises(pytest.fail.Exception):
        configured_integration_db_path()


def test_configured_existing_integration_db_is_returned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    configured = tmp_path / "scope-test.db"
    configured.write_bytes(b"sqlite-placeholder")
    monkeypatch.setenv(INTEGRATION_DB_ENV, str(configured))

    assert configured_integration_db_path() == configured
