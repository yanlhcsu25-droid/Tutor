ALTER TABLE paper ADD COLUMN root_paper_id VARCHAR(36) REFERENCES paper(id);
ALTER TABLE paper ADD COLUMN parent_version_id VARCHAR(36) REFERENCES paper(id);

UPDATE paper SET root_paper_id = id WHERE root_paper_id IS NULL;
