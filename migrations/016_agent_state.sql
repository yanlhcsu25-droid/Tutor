-- 016_agent_state.sql
-- Phase 1: durable per-conversation workspace pointer + Agent lifecycle state.
--
-- These tables are additive. They store only ID pointers / lifecycle phase and
-- do NOT duplicate Paper content, GeneratePaperInput, or business JSON.
-- Existing Agent behavior is untouched in this phase.

CREATE TABLE conversation_workspace (
    conversation_id VARCHAR(120) PRIMARY KEY,
    active_type VARCHAR(40),
    current_paper_id VARCHAR(36),
    current_version_id VARCHAR(36),
    pending_generation_id VARCHAR(36),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE agent_runtime_state (
    conversation_id VARCHAR(120) PRIMARY KEY,
    phase VARCHAR(20) NOT NULL DEFAULT 'idle',
    task_type VARCHAR(40),
    waiting_for VARCHAR(40),
    revision INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
