"""
services/sme/base_sme.py

Base class for all SME (Subject Matter Expert) agents.

Design rules (from CLAUDE.md — do not violate):
  1. Per-request AsyncAnthropic client — NEVER a module-level singleton.
     Module-level clients break under concurrent requests.
  2. Cache key = sha256(prompt_version + session_id + domain + question).
     Changing the system prompt automatically busts the cache because
     prompt_version is derived from the prompt hash in each SME subclass.
  3. Two-tier cache: Redis L1 (fast, TTL-based) → Postgres L2 (persistent).
  4. All Claude calls go through call_api_with_retry().
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from anthropic import AsyncAnthropic

from shared.constants import MAX_TOKENS_SME, MODEL, SME_CACHE_TTL_S
from shared.retry import call_api_with_retry

logger = logging.getLogger(__name__)


def _prompt_version(system_prompt: str) -> str:
    """Derive a 16-char version token from the system prompt content.
    Changing the prompt text automatically invalidates old cache entries."""
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def build_cache_key(
    session_id: str,
    domain: str,
    question: str,
    prompt_version: str,
) -> str:
    """
    Compute the SME cache lookup key.
    key = sha256(prompt_version + session_id + domain + question)
    """
    raw = prompt_version + session_id + domain + question
    return hashlib.sha256(raw.encode()).hexdigest()


class BaseSME:
    """
    Stateless SME agent base class.

    Subclasses must set:
        domain        = "<domain name>"
        system_prompt = "<expert system prompt>"

    Instantiate fresh per request — NEVER share across requests.
    """

    domain: str = "base"
    system_prompt: str = "You are a subject matter expert."

    def __init__(self) -> None:
        # Default prompt_version from class attribute (may be overridden per-request from DB).
        self._prompt_version = _prompt_version(self.system_prompt)

    async def _effective_prompt(self, pool: asyncpg.Pool | None) -> str:
        """Return the DB-stored prompt override if present, else the class default."""
        if pool is None:
            return self.system_prompt
        try:
            row = await pool.fetchrow(
                "SELECT content FROM system_prompts WHERE agent_role = $1",
                f"sme-{self.domain}",
            )
            return row["content"] if row else self.system_prompt
        except Exception:  # noqa: BLE001
            return self.system_prompt

    async def answer(
        self,
        question: str,
        session_id: str,
        redis_client: aioredis.Redis | None,  # type: ignore[type-arg]
        pool: asyncpg.Pool | None,
    ) -> dict[str, Any]:
        """
        Answer a question, using the two-tier cache when available.

        Args:
            question:     The question to answer.
            session_id:   Current workshop session ID (used in cache key).
            redis_client: Shared Redis client (L1 cache).
            pool:         asyncpg pool (L2 persistent cache).

        Returns:
            {"answer": str, "domain": str, "cached": bool}
        """
        # Load effective prompt (DB override wins over class default)
        effective_prompt = await self._effective_prompt(pool)
        prompt_ver = _prompt_version(effective_prompt)

        cache_key = build_cache_key(session_id, self.domain, question, prompt_ver)

        # ── L1: Redis ─────────────────────────────────────────────────────────
        if redis_client is not None:
            cached = await self._redis_get(redis_client, cache_key)
            if cached is not None:
                logger.debug("SME cache HIT (Redis) domain=%s key=%.12s", self.domain, cache_key)
                return {**cached, "cached": True}

        # ── L2: Postgres ──────────────────────────────────────────────────────
        if pool is not None:
            cached = await self._db_get(pool, cache_key, prompt_ver)
            if cached is not None:
                logger.debug("SME cache HIT (Postgres) domain=%s key=%.12s", self.domain, cache_key)
                # Warm Redis so next hit is fast
                if redis_client is not None:
                    await self._redis_set(redis_client, cache_key, cached)
                return {**cached, "cached": True}

        # ── Cache miss: call Claude ────────────────────────────────────────────
        # Per-request client — NOT module-level (breaks under concurrency)
        client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        response = await call_api_with_retry(
            client.messages.create,
            model=MODEL,
            max_tokens=MAX_TOKENS_SME,
            system=effective_prompt,
            messages=[{"role": "user", "content": question}],
        )

        answer_text = response.content[0].text if response.content else ""
        result: dict[str, Any] = {"answer": answer_text, "domain": self.domain}

        # ── Store in both cache tiers ──────────────────────────────────────────
        if redis_client is not None:
            await self._redis_set(redis_client, cache_key, result)

        if pool is not None:
            await self._db_set(pool, cache_key, session_id, result, prompt_ver)

        logger.info("SME cache MISS — called Claude domain=%s key=%.12s", self.domain, cache_key)
        return {**result, "cached": False}

    # ── Redis helpers ──────────────────────────────────────────────────────────

    async def _redis_get(
        self, client: aioredis.Redis, key: str  # type: ignore[type-arg]
    ) -> dict[str, Any] | None:
        try:
            raw = await client.get(f"sme:{key}")
            if raw:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SME Redis GET failed (non-fatal): %s", exc)
        return None

    async def _redis_set(
        self, client: aioredis.Redis, key: str, value: dict[str, Any]  # type: ignore[type-arg]
    ) -> None:
        try:
            await client.setex(f"sme:{key}", SME_CACHE_TTL_S, json.dumps(value))
        except Exception as exc:  # noqa: BLE001
            logger.warning("SME Redis SET failed (non-fatal): %s", exc)

    # ── Postgres helpers ───────────────────────────────────────────────────────

    async def _db_get(self, pool: asyncpg.Pool, key: str, prompt_ver: str) -> dict[str, Any] | None:
        try:
            row = await pool.fetchrow(
                """
                SELECT response FROM sme_cache
                WHERE query_hash = $1
                  AND prompt_version = $2
                  AND expires_at > now()
                LIMIT 1
                """,
                key,
                prompt_ver,
            )
            if row:
                raw = row["response"]
                # asyncpg returns jsonb as a dict already; handle both
                return raw if isinstance(raw, dict) else json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SME Postgres GET failed (non-fatal): %s", exc)
        return None

    async def _db_set(
        self,
        pool: asyncpg.Pool,
        key: str,
        session_id: str,
        value: dict[str, Any],
        prompt_ver: str,
    ) -> None:
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=SME_CACHE_TTL_S)
            await pool.execute(
                """
                INSERT INTO sme_cache
                    (id, session_id, domain, query_hash, prompt_version, response, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                ON CONFLICT (query_hash, prompt_version)
                DO UPDATE SET response = EXCLUDED.response, expires_at = EXCLUDED.expires_at
                """,
                str(uuid.uuid4()),
                session_id,
                self.domain,
                key,
                prompt_ver,
                json.dumps(value),
                expires_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SME Postgres SET failed (non-fatal): %s", exc)
