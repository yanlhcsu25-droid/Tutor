-- 014_question_chapter_ownership.sql
-- Schema migration. Legacy backfill is performed by create_schema() in Python.

ALTER TABLE question
ADD COLUMN curriculum_chapter_id VARCHAR(36) REFERENCES curriculum_node(id);

CREATE INDEX IF NOT EXISTS ix_question_curriculum_chapter_id
ON question (curriculum_chapter_id);
