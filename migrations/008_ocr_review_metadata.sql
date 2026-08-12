ALTER TABLE ocr_import_draft ADD COLUMN knowledge_points_json JSON;
ALTER TABLE ocr_import_draft ADD COLUMN difficulty_level INTEGER;
ALTER TABLE ocr_import_draft ADD COLUMN formal_question_id VARCHAR(36);
ALTER TABLE ocr_import_draft ADD COLUMN revision_of_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_ocr_import_draft_formal_question_id ON ocr_import_draft(formal_question_id);
CREATE INDEX IF NOT EXISTS ix_ocr_import_draft_revision_of_id ON ocr_import_draft(revision_of_id);
