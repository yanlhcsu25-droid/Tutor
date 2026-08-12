ALTER TABLE ocr_import_draft ADD COLUMN content_confirmed BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_ocr_import_draft_content_confirmed ON ocr_import_draft(content_confirmed);
