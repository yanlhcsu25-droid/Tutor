-- 015_teaching_design.sql
-- T1: versioned TeachingDesign source of truth and Paper provenance.

CREATE TABLE teaching_design_version (
    id VARCHAR(36) PRIMARY KEY,
    design_key VARCHAR(36) NOT NULL,
    owner_key VARCHAR(120) NOT NULL,
    source_conversation_id VARCHAR(120) NOT NULL,
    parent_version_id VARCHAR(36) REFERENCES teaching_design_version(id),
    version INTEGER NOT NULL,
    status VARCHAR(30) NOT NULL,
    title VARCHAR(255) NOT NULL,
    design_json JSON NOT NULL,

    created_by_run_id VARCHAR(36),
    source_user_message TEXT,
    change_reason TEXT,
    created_at DATETIME NOT NULL,

    confirmed_by_run_id VARCHAR(36),
    confirmed_at DATETIME,

    superseded_by_version_id VARCHAR(36) REFERENCES teaching_design_version(id),
    superseded_at DATETIME,

    UNIQUE (design_key, version)
);

CREATE INDEX ix_teaching_design_version_design_key
ON teaching_design_version (design_key);

CREATE INDEX ix_teaching_design_version_owner_key
ON teaching_design_version (owner_key);

CREATE INDEX ix_teaching_design_version_status
ON teaching_design_version (status);

CREATE INDEX ix_teaching_design_version_created_by_run_id
ON teaching_design_version (created_by_run_id);

CREATE INDEX ix_teaching_design_version_confirmed_by_run_id
ON teaching_design_version (confirmed_by_run_id);

CREATE TABLE active_teaching_design (
    owner_key VARCHAR(120) NOT NULL,
    conversation_id VARCHAR(120) NOT NULL,
    design_version_id VARCHAR(36) NOT NULL
        REFERENCES teaching_design_version(id),
    activated_by_run_id VARCHAR(36),
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (owner_key, conversation_id)
);

ALTER TABLE paper
ADD COLUMN teaching_design_version_id VARCHAR(36)
REFERENCES teaching_design_version(id);

CREATE INDEX ix_paper_teaching_design_version_id
ON paper (teaching_design_version_id);
