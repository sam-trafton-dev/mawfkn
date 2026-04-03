-- 001_init.sql — MAWF database schema initialisation
-- Run automatically by postgres container on first start via /docker-entrypoint-initdb.d/

-- Enable pgcrypto for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── sessions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workshop_name TEXT        NOT NULL,
    task_spec     JSONB       NOT NULL DEFAULT '{}',
    status        TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'running', 'completed', 'failed', 'stuck')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_status      ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at  ON sessions(created_at DESC);

-- ── agent_states ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_states (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    agent_role    TEXT        NOT NULL,
    system_prompt TEXT        NOT NULL DEFAULT '',
    state         JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agent_states_session ON agent_states(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_states_session_role
    ON agent_states(session_id, agent_role);

-- ── iterations ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS iterations (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    loop_n         INT         NOT NULL CHECK (loop_n >= 1),
    outputs        JSONB       NOT NULL DEFAULT '{}',
    test_pass_rate FLOAT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_iterations_session    ON iterations(session_id);
CREATE INDEX IF NOT EXISTS idx_iterations_session_n  ON iterations(session_id, loop_n);

-- ── events ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    agent_role  TEXT        NOT NULL,
    event_type  TEXT        NOT NULL,
    payload     JSONB       NOT NULL DEFAULT '{}',
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_session    ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_type       ON events(event_type);

-- ── sme_cache ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sme_cache (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    domain         TEXT        NOT NULL,
    query_hash     TEXT        NOT NULL,
    prompt_version TEXT        NOT NULL,
    response       JSONB       NOT NULL DEFAULT '{}',
    expires_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sme_cache_session    ON sme_cache(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sme_cache_hash ON sme_cache(query_hash, prompt_version);
CREATE INDEX IF NOT EXISTS idx_sme_cache_expires    ON sme_cache(expires_at);

-- ── updated_at trigger ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sessions_updated_at ON sessions;
CREATE TRIGGER trg_sessions_updated_at
    BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
