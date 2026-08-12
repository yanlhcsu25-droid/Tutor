CREATE TABLE IF NOT EXISTS paper_operation_history (
    id VARCHAR(36) PRIMARY KEY,
    root_paper_id VARCHAR(36) NOT NULL REFERENCES paper(id),
    source_paper_id VARCHAR(36) NOT NULL REFERENCES paper(id),
    result_paper_id VARCHAR(36) NOT NULL UNIQUE REFERENCES paper(id),
    operation_type VARCHAR(50) NOT NULL,
    operations_json JSON NOT NULL,
    before_state_json JSON NOT NULL,
    after_state_json JSON NOT NULL,
    undone_operation_id VARCHAR(36) REFERENCES paper_operation_history(id),
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_paper_operation_history_root
ON paper_operation_history(root_paper_id);

CREATE INDEX IF NOT EXISTS ix_paper_operation_history_result
ON paper_operation_history(result_paper_id);
