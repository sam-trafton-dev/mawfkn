-- 002_system_prompts.sql
-- Persists editable system prompts for all agents.
-- Each agent seeds its own default on startup (INSERT ... ON CONFLICT DO NOTHING).
-- Edits via the obs-app UI write here and are picked up on the next agent request.

CREATE TABLE IF NOT EXISTS system_prompts (
    agent_role  TEXT        PRIMARY KEY,
    content     TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
