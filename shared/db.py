"""
shared/db.py — asyncpg connection pool, used by all Python services.

Usage:
    from shared.db import get_pool, close_pool, log_event

    # In FastAPI lifespan:
    async with lifespan(app):
        pool = await get_pool()

    # Log an event anywhere:
    await log_event(pool, session_id, agent_role="coder", event_type="task_started", payload={})
"""

from __future__ import annotations

import logging
import os
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    """
    Return a plain asyncpg DSN from DATABASE_URL.
    Strips the '+asyncpg' driver prefix if present (SQLAlchemy style).
    """
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def get_pool(min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    """Return the module-level connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=_dsn(),
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
        )
        logger.info("asyncpg pool created (min=%d max=%d)", min_size, max_size)
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool. Call from FastAPI shutdown lifespan."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


# ── Convenience helpers ────────────────────────────────────────────────────────

async def log_event(
    pool: asyncpg.Pool,
    session_id: str,
    agent_role: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append a row to the events table (append-only audit log)."""
    import json
    await pool.execute(
        """
        INSERT INTO events (session_id, agent_role, event_type, payload)
        VALUES ($1, $2, $3, $4::jsonb)
        """,
        session_id,
        agent_role,
        event_type,
        json.dumps(payload or {}),
    )


async def update_session_status(pool: asyncpg.Pool, session_id: str, status: str) -> None:
    """Update sessions.status and trigger the updated_at timestamp."""
    await pool.execute(
        "UPDATE sessions SET status = $1 WHERE id = $2",
        status,
        session_id,
    )


async def emit_agent_event(
    pool: asyncpg.Pool,
    redis: Any,
    session_id: str,
    agent_role: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Write an event to the Postgres events table AND publish to Redis pub/sub.
    Use this from agent services so errors appear in the obs-app event feed,
    not just in Docker logs.
    """
    from datetime import datetime, timezone

    import redis.asyncio as aioredis

    from shared.redis_client import events_channel, publish

    # Postgres (durable)
    try:
        await log_event(pool, session_id, agent_role, event_type, payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("emit_agent_event: DB write failed: %s", exc)

    # Redis (real-time stream to obs-app)
    try:
        await publish(redis, events_channel(session_id), {
            "agent_role": agent_role,
            "event_type": event_type,
            "payload": payload,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_agent_event: Redis publish failed (non-fatal): %s", exc)


async def get_agent_prompt(pool: asyncpg.Pool, agent_role: str, default: str) -> str:
    """Return the current system prompt for an agent, falling back to default."""
    row = await pool.fetchrow(
        "SELECT content FROM system_prompts WHERE agent_role = $1", agent_role
    )
    return row["content"] if row else default


async def seed_agent_prompt(pool: asyncpg.Pool, agent_role: str, default_content: str) -> None:
    """Insert the default prompt only if no row exists yet (preserves user edits)."""
    await pool.execute(
        """
        INSERT INTO system_prompts (agent_role, content)
        VALUES ($1, $2)
        ON CONFLICT (agent_role) DO NOTHING
        """,
        agent_role,
        default_content,
    )


async def set_agent_prompt(pool: asyncpg.Pool, agent_role: str, content: str) -> None:
    """Upsert a system prompt, updating updated_at."""
    await pool.execute(
        """
        INSERT INTO system_prompts (agent_role, content, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (agent_role) DO UPDATE
            SET content = EXCLUDED.content, updated_at = now()
        """,
        agent_role,
        content,
    )


async def list_agent_prompts(pool: asyncpg.Pool) -> list[dict]:
    """Return all agent prompts ordered by agent_role."""
    rows = await pool.fetch(
        "SELECT agent_role, content, updated_at FROM system_prompts ORDER BY agent_role"
    )
    return [
        {
            "agent_role": r["agent_role"],
            "content": r["content"],
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


async def write_iteration(
    pool: asyncpg.Pool,
    session_id: str,
    loop_n: int,
    outputs: dict[str, Any],
    test_pass_rate: float | None = None,
) -> str:
    """Insert an iterations row and return its UUID."""
    import json
    row = await pool.fetchrow(
        """
        INSERT INTO iterations (session_id, loop_n, outputs, test_pass_rate)
        VALUES ($1, $2, $3::jsonb, $4)
        RETURNING id
        """,
        session_id,
        loop_n,
        json.dumps(outputs),
        test_pass_rate,
    )
    return str(row["id"])
