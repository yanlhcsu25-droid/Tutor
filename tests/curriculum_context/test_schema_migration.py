from sqlalchemy import create_engine, inspect

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import Textbook


def test_existing_textbook_table_is_backfilled_with_directory_revision(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"

    legacy_engine = create_engine(database_url)
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE textbook (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                edition VARCHAR(120),
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO textbook
                (id, name, edition, description, is_active, created_at)
            VALUES
                ('legacy-book', '旧教材', NULL, NULL, 1, CURRENT_TIMESTAMP)
            """
        )

    create_schema(database_url)

    columns = {
        item["name"]: item
        for item in inspect(create_engine(database_url)).get_columns("textbook")
    }
    assert "directory_revision" in columns

    factory = build_session_factory(database_url)
    with factory.begin() as session:
        book = session.get(Textbook, "legacy-book")
        assert book is not None
        assert book.directory_revision == 1
