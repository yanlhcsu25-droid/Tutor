-- 013: Run-Level Tracing。
-- 为 teacher_agent_run_trace 增加 run_id 关联键与生命周期 / 状态字段，
-- 并新增 teacher_agent_span 表，支撑 conversation_id → run_id → spans 的两级关联。
-- 对应 src/calculus_agent/db.py create_schema() 中的幂等 ALTER / create_all 块。
-- 注意：create_all 会为 teacher_agent_span 自动建表；既有 teacher_agent_run_trace
-- 加列由 create_schema 在应用启动时幂等执行。本文件仅作记录。

ALTER TABLE teacher_agent_run_trace ADD COLUMN run_id VARCHAR(36);
CREATE UNIQUE INDEX IF NOT EXISTS ix_teacher_agent_run_trace_run_id ON teacher_agent_run_trace (run_id);
ALTER TABLE teacher_agent_run_trace ADD COLUMN status VARCHAR(40) NOT NULL DEFAULT 'received';
ALTER TABLE teacher_agent_run_trace ADD COLUMN started_at DATETIME;
ALTER TABLE teacher_agent_run_trace ADD COLUMN ended_at DATETIME;
ALTER TABLE teacher_agent_run_trace ADD COLUMN latency_ms INTEGER;
ALTER TABLE teacher_agent_run_trace ADD COLUMN agent_name VARCHAR(60) NOT NULL DEFAULT 'teacher_agent';
ALTER TABLE teacher_agent_run_trace ADD COLUMN state_before_json JSON;
ALTER TABLE teacher_agent_run_trace ADD COLUMN state_after_json JSON;

CREATE TABLE IF NOT EXISTS teacher_agent_span (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    span_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36),
    parent_span_id VARCHAR(36),
    span_type VARCHAR(30) NOT NULL,
    name VARCHAR(120) NOT NULL,
    status VARCHAR(30) NOT NULL,
    started_at DATETIME,
    ended_at DATETIME,
    latency_ms INTEGER,
    input_json JSON,
    output_json JSON
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_teacher_agent_span_span_id ON teacher_agent_span (span_id);
CREATE INDEX IF NOT EXISTS ix_teacher_agent_span_run_id ON teacher_agent_span (run_id);
CREATE INDEX IF NOT EXISTS ix_teacher_agent_span_parent_span_id ON teacher_agent_span (parent_span_id);
CREATE INDEX IF NOT EXISTS ix_teacher_agent_span_span_type ON teacher_agent_span (span_type);
