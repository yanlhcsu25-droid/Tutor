-- Persistent blueprint -> paper -> validation workflow (SQLite/PostgreSQL-compatible DDL).
CREATE TABLE IF NOT EXISTS paper_blueprint (
  id VARCHAR(36) PRIMARY KEY, title VARCHAR(255) NOT NULL, blueprint_json JSON NOT NULL,
  status VARCHAR(20) NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS paper (
  id VARCHAR(36) PRIMARY KEY, blueprint_id VARCHAR(36) NOT NULL REFERENCES paper_blueprint(id),
  version INTEGER NOT NULL, status VARCHAR(20) NOT NULL, title VARCHAR(255) NOT NULL,
  total_score INTEGER NOT NULL, validation_status VARCHAR(20) NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_item (
  id VARCHAR(36) PRIMARY KEY, paper_id VARCHAR(36) NOT NULL REFERENCES paper(id),
  question_id VARCHAR(36) NOT NULL REFERENCES question(id), section VARCHAR(40) NOT NULL,
  position INTEGER NOT NULL, score REAL NOT NULL, locked BOOLEAN NOT NULL,
  UNIQUE (paper_id, question_id), UNIQUE (paper_id, position)
);
CREATE TABLE IF NOT EXISTS validation_report (
  id VARCHAR(36) PRIMARY KEY, paper_id VARCHAR(36) NOT NULL REFERENCES paper(id),
  passed BOOLEAN NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS constraint_violation (
  id VARCHAR(36) PRIMARY KEY, report_id VARCHAR(36) NOT NULL REFERENCES validation_report(id),
  code VARCHAR(80) NOT NULL, field VARCHAR(255) NOT NULL, required_json JSON,
  actual_json JSON, question_ids_json JSON NOT NULL, repairable BOOLEAN NOT NULL,
  message TEXT NOT NULL
);
