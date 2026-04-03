-- 002_system_prompts.sql
-- Persists editable system prompts for all agents (table name: agent_prompts).
-- Each agent seeds its own default on startup (INSERT ... ON CONFLICT DO NOTHING).
-- Edits via the obs-app UI write here and are picked up on the next agent request.
--
-- Legacy: an earlier revision created "system_prompts". Code uses "agent_prompts".
-- We create agent_prompts and migrate rows from system_prompts if present.

CREATE TABLE IF NOT EXISTS agent_prompts (
    agent_role  TEXT        PRIMARY KEY,
    content     TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One-time migration from legacy table name
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'system_prompts'
  ) THEN
    INSERT INTO agent_prompts (agent_role, content, updated_at)
    SELECT agent_role, content, updated_at FROM system_prompts
    ON CONFLICT (agent_role) DO NOTHING;
    DROP TABLE system_prompts;
  END IF;
END $$;
