import tempfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from calculus_agent.db import build_session_factory, create_schema


def create_isolated_test_session() -> Session:
    """Public helper: build a brand-new isolated test Session.

    Each call returns a fresh Session backed by a temporary SQLite database
    in its own tmp directory. The caller is responsible for ``session.close()``
    (or wrapping in a ``with factory.begin()`` block) when done.

    Used by both the ``session`` pytest fixture below and the standalone
    Eval Runner (``tests/evals/runner.py``), so eval cases can run outside
    pytest against the exact same test DB bootstrap logic.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="test-isolated-"))
    database_url = f"sqlite:///{tmp_dir / 'test.db'}"
    create_schema(database_url)
    factory = build_session_factory(database_url)
    return factory()


@pytest.fixture
def session(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_schema(database_url)
    factory = build_session_factory(database_url)
    with factory.begin() as value:
        yield value
