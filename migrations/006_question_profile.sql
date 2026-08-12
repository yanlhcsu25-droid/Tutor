CREATE TABLE IF NOT EXISTS question_profile (
    id VARCHAR(36) PRIMARY KEY,
    question_id VARCHAR(36) NOT NULL REFERENCES question(id),
    profile_version INTEGER NOT NULL,
    difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    estimated_time_min INTEGER NOT NULL CHECK (estimated_time_min BETWEEN 1 AND 180),
    reasoning_depth INTEGER NOT NULL CHECK (reasoning_depth BETWEEN 1 AND 5),
    calculation_load INTEGER NOT NULL CHECK (calculation_load BETWEEN 1 AND 5),
    knowledge_depth INTEGER NOT NULL CHECK (knowledge_depth BETWEEN 1 AND 5),
    comprehensive_level INTEGER NOT NULL CHECK (comprehensive_level BETWEEN 1 AND 5),
    confidence FLOAT NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    profile_source VARCHAR(30) NOT NULL,
    profile_status VARCHAR(30) NOT NULL,
    reason TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    reviewed_at DATETIME,
    UNIQUE (question_id, profile_version)
);

CREATE INDEX IF NOT EXISTS ix_question_profile_question_id
ON question_profile(question_id);

CREATE INDEX IF NOT EXISTS ix_question_profile_status
ON question_profile(profile_status);

CREATE INDEX IF NOT EXISTS ix_question_profile_difficulty
ON question_profile(difficulty);
