"""
shared/redis_client.py — redis-py async client setup, used by all Python services.

Provides:
  - get_client()        : module-level redis.asyncio.Redis singleton
  - close_client()      : graceful shutdown
  - publish()           : publish a JSON message to a channel
  - subscribe_loop()    : async generator that yields messages from a channel
  - push_dead_letter()  : push a failed message to the dead-letter list

Channel naming conventions (all prefixed with session_id):
  {session_id}:assign:{agent}   — orchestrator → agent task assignment
  {session_id}:result:{agent}   — agent → orchestrator result
  {session_id}:events           — broadcast event stream (obs-app subscribes)

Dead-letter key:
  {session_id}:dlq              — RPUSH timed-out or failed messages here
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None  # type: ignore[type-arg]


def get_client() -> aioredis.Redis:  # type: ignore[type-arg]
    """Return the module-level Redis client, creating it on first call."""
    global _client
    if _client is None:
        url = os.environ["REDIS_URL"]
        _client = aioredis.from_url(url, decode_responses=True)
        logger.info("Redis client created from %s", url)
    return _client


async def close_client() -> None:
    """Gracefully close the Redis client. Call from FastAPI shutdown lifespan."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Redis client closed")


# ── Pub/sub helpers ────────────────────────────────────────────────────────────

async def publish(client: aioredis.Redis, channel: str, message: dict[str, Any]) -> None:  # type: ignore[type-arg]
    """Publish a JSON-encoded message to a Redis channel."""
    await client.publish(channel, json.dumps(message))
    logger.debug("published to %s: %s", channel, message)


async def subscribe_loop(
    client: aioredis.Redis,  # type: ignore[type-arg]
    channel: str,
    timeout_s: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    Async generator that yields decoded JSON messages from a pub/sub channel.
    Stops after `timeout_s` seconds of silence if provided.

    Usage:
        async for msg in subscribe_loop(client, channel, timeout_s=30):
            handle(msg)
    """
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    logger.debug("subscribed to %s", channel)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=timeout_s or 0.1,
            )
            if message is None:
                if timeout_s is not None:
                    return
                continue
            if message["type"] == "message":
                try:
                    yield json.loads(message["data"])
                except json.JSONDecodeError:
                    logger.warning("non-JSON message on %s: %s", channel, message["data"])
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


# ── Dead-letter queue ──────────────────────────────────────────────────────────

async def push_dead_letter(
    client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: str,
    reason: str,
    original_message: dict[str, Any] | None = None,
) -> None:
    """Push a failed/timed-out message to the session dead-letter list."""
    entry = {
        "reason": reason,
        "original": original_message or {},
    }
    key = f"{session_id}:dlq"
    await client.rpush(key, json.dumps(entry))
    logger.error("dead-letter [%s]: %s", key, reason)


# ── Channel name helpers ───────────────────────────────────────────────────────

def assign_channel(session_id: str, agent: str) -> str:
    return f"{session_id}:assign:{agent}"


def result_channel(session_id: str, agent: str) -> str:
    return f"{session_id}:result:{agent}"


def events_channel(session_id: str) -> str:
    return f"{session_id}:events"
