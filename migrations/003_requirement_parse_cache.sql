CREATE TABLE IF NOT EXISTS requirement_parse_cache (
  requirement_hash VARCHAR(64) PRIMARY KEY,
  requirement_text TEXT NOT NULL,
  blueprint_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL
);
