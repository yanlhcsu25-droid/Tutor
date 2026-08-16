-- 受控 AI 自动发布审计字段（仅增量添加，不改写历史记录）。
ALTER TABLE ocr_import_draft ADD COLUMN ai_review_json JSON;
ALTER TABLE ocr_import_draft ADD COLUMN publish_source VARCHAR(30);
ALTER TABLE ocr_import_draft ADD COLUMN quality_sample_required BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE ocr_import_draft ADD COLUMN published_at DATETIME;

ALTER TABLE question ADD COLUMN publish_source VARCHAR(30) NOT NULL DEFAULT 'manual';
ALTER TABLE question ADD COLUMN ai_review_json JSON;
ALTER TABLE question ADD COLUMN quality_sample_required BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE question ADD COLUMN published_at DATETIME;

CREATE INDEX ix_question_publish_source ON question (publish_source);
CREATE INDEX ix_question_quality_sample_required ON question (quality_sample_required);
