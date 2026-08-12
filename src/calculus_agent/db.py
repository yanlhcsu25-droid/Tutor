from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


@lru_cache
def build_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


@lru_cache
def build_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=build_engine(database_url), expire_on_commit=False)


@lru_cache
def create_schema(database_url: str) -> None:
    from calculus_agent import models  # noqa: F401

    engine = build_engine(database_url)
    Base.metadata.create_all(engine)

    # paper 表迁移
    paper_columns = {item["name"] for item in inspect(engine).get_columns("paper")}
    with engine.begin() as connection:
        if "root_paper_id" not in paper_columns:
            connection.exec_driver_sql(
                "ALTER TABLE paper ADD COLUMN root_paper_id VARCHAR(36) REFERENCES paper(id)"
            )
        if "parent_version_id" not in paper_columns:
            connection.exec_driver_sql(
                "ALTER TABLE paper ADD COLUMN parent_version_id VARCHAR(36) REFERENCES paper(id)"
            )

    # ocr_task 表迁移
    ocr_task_columns = {item["name"] for item in inspect(engine).get_columns("ocr_task")}
    with engine.begin() as connection:
        if "page_images_json" not in ocr_task_columns:
            connection.exec_driver_sql(
                'ALTER TABLE ocr_task ADD COLUMN page_images_json JSON DEFAULT "[]"'
            )

    # ocr_block 表迁移
    ocr_block_columns = {item["name"] for item in inspect(engine).get_columns("ocr_block")}
    with engine.begin() as connection:
        if "page_number" not in ocr_block_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_block ADD COLUMN page_number INTEGER DEFAULT 1"
            )

    # OCR source 导入布局迁移；兼容 create_schema 管理的既有 SQLite 数据库。
    source_columns = {item["name"] for item in inspect(engine).get_columns("ocr_import_source")}
    with engine.begin() as connection:
        if "layout_json" not in source_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_source ADD COLUMN layout_json JSON"
            )

    # ocr_import_draft 匹配状态迁移；仅添加列，不改名、不删列。
    draft_columns = {item["name"] for item in inspect(engine).get_columns("ocr_import_draft")}
    with engine.begin() as connection:
        if "match_status" not in draft_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN match_status VARCHAR(20) NOT NULL DEFAULT 'matched'"
            )
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN match_method VARCHAR(20) NOT NULL DEFAULT 'inline'"
            )
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN review_note TEXT"
            )
            # 历史数据回填（仅首次加列时执行一次，幂等）：
            #   - inline / legacy（无 layout 或 solution_mode=inline）普通 OCR：
            #     本身不是题目答案分离匹配，默认视为已配对成功 -> matched
            #   - separate 套卷：若无可靠匹配记录，一律置 unknown，
            #     必须重新切题/匹配后才得到 matched/missing_answer/ambiguous，
            #     禁止把未知历史 separate 草稿自动认定为已配对成功。
            connection.exec_driver_sql(
                """
                UPDATE ocr_import_draft SET match_status = CASE
                    WHEN source_id IN (
                        SELECT id FROM ocr_import_source
                        WHERE json_extract(layout_json, '$.solution_mode') = 'separate'
                    ) THEN 'unknown'
                    ELSE 'matched'
                END
                WHERE match_status = 'matched'
                """
            )

    # curriculum_node 迁移 — textbook_id
    cur_columns = {item["name"] for item in inspect(engine).get_columns("curriculum_node")}
    with engine.begin() as connection:
        if "textbook_id" not in cur_columns:
            connection.exec_driver_sql(
                "ALTER TABLE curriculum_node ADD COLUMN textbook_id VARCHAR(36) REFERENCES textbook(id)"
            )

    # 正式题原地编辑与软删除。
    question_columns = {item["name"] for item in inspect(engine).get_columns("question")}
    with engine.begin() as connection:
        if "is_active" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
            )
        if "knowledge_match_status" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN knowledge_match_status VARCHAR(30) NOT NULL DEFAULT 'current'"
            )
            connection.exec_driver_sql(
                "UPDATE question SET knowledge_match_status='unmatched' "
                "WHERE NOT EXISTS (SELECT 1 FROM question_knowledge_link l WHERE l.question_id=question.id)"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_is_active ON question (is_active)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_knowledge_match_status "
            "ON question (knowledge_match_status)"
        )


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    factory = build_session_factory(database_url)
    with factory.begin() as session:
        yield session
