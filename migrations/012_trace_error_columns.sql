-- 012: Teacher Agent 运行 trace 增加失败错误明细列，用于可观测性。
-- 对应 src/calculus_agent/db.py create_schema() 中的幂等 ALTER 块。
-- 注意：create_all 不会为既有表加列，因此必须通过代码 ALTER 补齐。
-- 本文件仅作记录；实际加列由 create_schema 在应用启动时幂等执行。

ALTER TABLE teacher_agent_run_trace ADD COLUMN error_code VARCHAR(80);
ALTER TABLE teacher_agent_run_trace ADD COLUMN error_type VARCHAR(120);
ALTER TABLE teacher_agent_run_trace ADD COLUMN error_message TEXT;
ALTER TABLE teacher_agent_run_trace ADD COLUMN error_stage VARCHAR(60);
