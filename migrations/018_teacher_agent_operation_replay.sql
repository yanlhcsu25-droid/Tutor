ALTER TABLE teacher_agent_run_trace ADD COLUMN request_fingerprint VARCHAR(64);
ALTER TABLE teacher_agent_run_trace ADD COLUMN result_json JSON;

CREATE INDEX IF NOT EXISTS ix_teacher_agent_run_trace_request_fingerprint
    ON teacher_agent_run_trace (request_fingerprint);
