"""
shared/db.py

Database connection management and helper functions for PostgreSQL.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _asyncpg_dsn(url: str) -> str:
    """
    asyncpg accepts only postgresql:// or postgres://.
    Strip SQLAlchemy's +asyncpg driver suffix if present (legacy env or docs).
    """
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url.removeprefix("postgresql+asyncpg://")
    if url.startswith("postgres+asyncpg://"):
        return "postgres://" + url.removeprefix("postgres+asyncpg://")
    return url


async def get_pool() -> asyncpg.Pool:
    """Get or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        raw = os.getenv("DATABASE_URL", "postgresql://mawf:mawf@postgres:5432/mawf")
        dsn = _asyncpg_dsn(raw)
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        logger.info("Created asyncpg connection pool")
    return _pool


async def close_pool() -> None:
    """Close the asyncpg connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Closed asyncpg connection pool")


async def seed_agent_prompt(pool: asyncpg.Pool, agent_role: str, default_prompt: str) -> None:
    """Insert a default prompt if one doesn't exist for this agent."""
    await pool.execute(
        """
        INSERT INTO agent_prompts (agent_role, content)
        VALUES ($1, $2)
        ON CONFLICT (agent_role) DO NOTHING
        """,
        agent_role,
        default_prompt,
    )


async def get_agent_prompt(pool: asyncpg.Pool, agent_role: str, default: str) -> str:
    """Retrieve the current system prompt for an agent, falling back to default."""
    row = await pool.fetchrow(
        "SELECT content FROM agent_prompts WHERE agent_role = $1",
        agent_role,
    )
    return row["content"] if row else default


async def set_agent_prompt(pool: asyncpg.Pool, agent_role: str, content: str) -> None:
    """Update or insert a system prompt for an agent."""
    await pool.execute(
        """
        INSERT INTO agent_prompts (agent_role, content, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (agent_role) DO UPDATE SET content = $2, updated_at = NOW()
        """,
        agent_role,
        content,
    )


async def list_agent_prompts(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all agent prompts with their metadata."""
    rows = await pool.fetch(
        "SELECT agent_role, content, updated_at FROM agent_prompts ORDER BY agent_role"
    )
    return [
        {
            "agent_role": row["agent_role"],
            "content": row["content"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]


async def log_event(
    pool: asyncpg.Pool,
    session_id: str,
    agent_role: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Log an event to the events table."""
    await pool.execute(
        """
        INSERT INTO events (session_id, agent_role, event_type, payload, ts)
        VALUES ($1, $2, $3, $4::jsonb, $5)
        """,
        session_id,
        agent_role,
        event_type,
        json.dumps(payload, default=str),
        datetime.now(timezone.utc),
    )


async def update_session_status(pool: asyncpg.Pool, session_id: str, status: str) -> None:
    """Update a session's status and updated_at timestamp."""
    await pool.execute(
        "UPDATE sessions SET status = $1, updated_at = NOW() WHERE id = $2",
        status,
        session_id,
    )


async def write_iteration(
    pool: asyncpg.Pool,
    session_id: str,
    loop_n: int,
    outputs: dict[str, Any],
    test_pass_rate: float | None,
) -> None:
    """
    Write an iteration record to the database.
    
    Args:
        pool: Database connection pool
        session_id: Session ID
        loop_n: Iteration number
        outputs: Agent outputs for this iteration
        test_pass_rate: QA pass rate (can be None if QA didn't run)
    """
    await pool.execute(
        """
        INSERT INTO iterations (session_id, loop_n, outputs, test_pass_rate, created_at)
        VALUES ($1, $2, $3::jsonb, $4, NOW())
        ON CONFLICT (session_id, loop_n) DO UPDATE
        SET outputs = $3::jsonb, test_pass_rate = $4, created_at = NOW()
        """,
        session_id,
        loop_n,
        json.dumps(outputs, default=str),
        test_pass_rate,  # Can be None - postgres will store as NULL
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
    Log event to database and publish to Redis.
    
    Convenience wrapper that handles both persistence and real-time streaming.
    """
    from shared.redis_client import events_channel, publish
    
    # Write to database
    await log_event(pool, session_id, agent_role, event_type, payload)
    
    # Publish to Redis for real-time streaming
    await publish(redis, events_channel(session_id), {
        "agent_role": agent_role,
        "event_type": event_type,
        "payload": payload,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
