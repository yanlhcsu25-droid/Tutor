from pathlib import Path
import sqlite3


MIGRATION = Path(__file__).parents[1] / "migrations" / "010_normalize_profile_source.sql"


def test_profile_source_migration_normalizes_legacy_value_and_is_idempotent(tmp_path):
    connection = sqlite3.connect(tmp_path / "profile-source.db")
    connection.execute("CREATE TABLE question_profile (profile_source VARCHAR(30) NOT NULL)")
    connection.executemany(
        "INSERT INTO question_profile(profile_source) VALUES (?)",
        [("ocr_publish",), ("human",), ("auto",)],
    )
    sql = MIGRATION.read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    rows = connection.execute(
        "SELECT profile_source, COUNT(*) FROM question_profile GROUP BY profile_source"
    ).fetchall()
    assert sorted(rows) == [("auto", 1), ("human", 2)]
