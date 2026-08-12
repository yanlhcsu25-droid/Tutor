CREATE TABLE IF NOT EXISTS question_knowledge_review (
  id VARCHAR(36) PRIMARY KEY, question_id VARCHAR(36) NOT NULL REFERENCES question(id),
  ai_prediction_json JSON NOT NULL, human_final_json JSON NOT NULL,
  deleted_by_human_json JSON NOT NULL, added_by_human_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_question_knowledge_review_question_id ON question_knowledge_review(question_id);
