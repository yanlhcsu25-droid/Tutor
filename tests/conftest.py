from pathlib import Path

import pytest

from calculus_agent.db import build_session_factory, create_schema


@pytest.fixture
def session(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    create_schema(database_url)
    factory = build_session_factory(database_url)
    with factory.begin() as value:
        yield value
