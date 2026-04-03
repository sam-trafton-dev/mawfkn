"""
shared/redis_client.py

Async Redis client utilities.
Single-client pattern: call get_client() from any service.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None  # type: ignore[type-arg]


def get_client() -> aioredis.Redis:  # type: ignore[type-arg]
    """Return the shared Redis client, creating it on first call."""
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        _client = aioredis.from_url(url, decode_responses=True)
        logger.info("Created Redis client: %s", url)
    return _client


async def close_client() -> None:
    """Close the Redis client (call on shutdown)."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
        logger.info("Closed Redis client")


def events_channel(session_id: str) -> str:
    """Return the pub/sub channel name for a session's events."""
    return f"events:{session_id}"


async def publish(client: aioredis.Redis, channel: str, message: dict[str, Any]) -> None:  # type: ignore[type-arg]
    """Publish a JSON message to a Redis pub/sub channel."""
    await client.publish(channel, json.dumps(message, default=str))


async def push_dead_letter(
    client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: str,
    reason: str,
    original_message: dict[str, Any],
) -> None:
    """
    Push a failed message to the dead-letter queue for later inspection.
    """
    dead_letter = {
        "session_id": session_id,
        "reason": reason,
        "original_message": original_message,
    }
    await client.rpush("dead_letter_queue", json.dumps(dead_letter, default=str))
    logger.warning("Pushed to dead-letter queue: %s", reason)
