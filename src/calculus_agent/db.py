from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, inspect, select
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

    # Phase 2C adjustment plan: the table itself is created by metadata;
    # existing local databases need the additive applied-version column.
    if "adjustment_plan" in inspect(engine).get_table_names():
        adjustment_columns = {item["name"] for item in inspect(engine).get_columns("adjustment_plan")}
        with engine.begin() as connection:
            if "applied_version_id" not in adjustment_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE adjustment_plan ADD COLUMN applied_version_id VARCHAR(36) REFERENCES paper(id)"
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
        if "content_sha256" not in source_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_source ADD COLUMN content_sha256 VARCHAR(64)"
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

    # AI 知识点预标注 Shadow Evaluation：为既有 OCR 草稿补充只读推荐快照。
    draft_columns = {item["name"] for item in inspect(engine).get_columns("ocr_import_draft")}
    with engine.begin() as connection:
        if "knowledge_shadow_json" not in draft_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN knowledge_shadow_json JSON"
            )
        if "ai_review_json" not in draft_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN ai_review_json JSON"
            )
        if "publish_source" not in draft_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN publish_source VARCHAR(30)"
            )
        if "quality_sample_required" not in draft_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN quality_sample_required BOOLEAN NOT NULL DEFAULT 0"
            )
        if "published_at" not in draft_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ocr_import_draft ADD COLUMN published_at DATETIME"
            )

    # curriculum_node 迁移 — textbook_id
    cur_columns = {item["name"] for item in inspect(engine).get_columns("curriculum_node")}
    with engine.begin() as connection:
        if "textbook_id" not in cur_columns:
            connection.exec_driver_sql(
                "ALTER TABLE curriculum_node ADD COLUMN textbook_id VARCHAR(36) REFERENCES textbook(id)"
            )

    # Existing textbook directories predate directory-to-taxonomy synchronization.
    # Backfill them idempotently so reopening the application immediately exposes
    # every directory entry in the knowledge review dropdown.
    from calculus_agent.knowledge.curriculum import sync_directory_knowledge_nodes
    from calculus_agent.models import CurriculumNode

    with Session(engine) as migration_session, migration_session.begin():
        textbook_nodes = list(migration_session.scalars(
            select(CurriculumNode).where(CurriculumNode.textbook_id.is_not(None))
        ).all())
        sync_directory_knowledge_nodes(migration_session, textbook_nodes)

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
        if "publish_source" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN publish_source VARCHAR(30) NOT NULL DEFAULT 'manual'"
            )
        if "ai_review_json" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN ai_review_json JSON"
            )
        if "quality_sample_required" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN quality_sample_required BOOLEAN NOT NULL DEFAULT 0"
            )
        if "published_at" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN published_at DATETIME"
            )
        if "updated_at" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN updated_at DATETIME"
            )
        if "curriculum_chapter_id" not in question_columns:
            connection.exec_driver_sql(
                "ALTER TABLE question ADD COLUMN curriculum_chapter_id VARCHAR(36) "
                "REFERENCES curriculum_node(id)"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_curriculum_chapter_id "
            "ON question (curriculum_chapter_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_is_active ON question (is_active)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_knowledge_match_status "
            "ON question (knowledge_match_status)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_publish_source ON question (publish_source)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_question_quality_sample_required "
            "ON question (quality_sample_required)"
        )

    # Phase 3.5: reconcile stale/materialized chapter ownership from current
    # knowledge + taxonomy. At the current local dataset size this full scan is
    # cheap; a versioned one-time migration can replace it at larger scale.
    from calculus_agent.questions.chapter_assignment import (
        reconcile_question_chapter_assignments,
    )

    with Session(engine) as migration_session, migration_session.begin():
        reconcile_question_chapter_assignments(migration_session)

    # Teacher Agent 运行 trace：失败错误明细（阶段 / 类型 / 文案），用于可观测性。
    # create_all 不会为既有表加列，这里做幂等 ALTER。
    trace_columns = {item["name"] for item in inspect(engine).get_columns("teacher_agent_run_trace")}
    with engine.begin() as connection:
        if "error_code" not in trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN error_code VARCHAR(80)"
            )
        if "error_type" not in trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN error_type VARCHAR(120)"
            )
        if "error_message" not in trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN error_message TEXT"
            )
        if "error_stage" not in trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN error_stage VARCHAR(60)"
            )

    # Run-Level Tracing（013）：为 teacher_agent_run_trace 增加 run_id 关联键与
    # 生命周期 / 状态字段，使每一次用户 Turn 都有唯一可查询的 run_id。
    # 新表 teacher_agent_span 由 create_all 自动建表，无需在此 ALTER。
    run_trace_columns = {item["name"] for item in inspect(engine).get_columns("teacher_agent_run_trace")}
    with engine.begin() as connection:
        if "run_id" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN run_id VARCHAR(36)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_teacher_agent_run_trace_run_id ON teacher_agent_run_trace (run_id)"
            )
        if "status" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN status VARCHAR(40) NOT NULL DEFAULT 'received'"
            )
        if "started_at" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN started_at DATETIME"
            )
        if "ended_at" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN ended_at DATETIME"
            )
        if "latency_ms" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN latency_ms INTEGER"
            )
        if "agent_name" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN agent_name VARCHAR(60) NOT NULL DEFAULT 'teacher_agent'"
            )
        if "state_before_json" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN state_before_json JSON"
            )
        if "state_after_json" not in run_trace_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teacher_agent_run_trace ADD COLUMN state_after_json JSON"
            )


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    factory = build_session_factory(database_url)
    with factory.begin() as session:
        yield session
