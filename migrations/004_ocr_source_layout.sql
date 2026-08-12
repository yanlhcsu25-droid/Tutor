-- Source-level OCR import layout. NULL is retained for legacy rows and means
-- the workbench must use its explicit inline legacy fallback.
ALTER TABLE ocr_import_source ADD COLUMN layout_json JSON;
