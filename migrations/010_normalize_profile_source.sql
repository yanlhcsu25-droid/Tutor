-- 历史 OCR 发布画像使用了业务流程值 ocr_publish。
-- profile_source 表示画像来源，OCR 审核发布属于人工画像来源。
UPDATE question_profile
SET profile_source = 'human'
WHERE profile_source = 'ocr_publish';
