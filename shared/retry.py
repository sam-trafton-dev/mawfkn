"""
shared/retry.py — call_api_with_retry() for all Anthropic API calls.

All services import from here. services/orchestrator/retry.py re-exports
this module for backwards compatibility.

Rule: Never call client.messages.create() directly in agent code.
      Always go through call_api_with_retry().
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_BASE_DELAY_S: float = 1.0
_MAX_DELAY_S: float = 60.0
_MAX_RETRIES: int = 8
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 529})


def _jitter(delay: float) -> float:
    """Add ±25% jitter to a delay value."""
    return delay * (0.75 + random.random() * 0.5)


async def call_api_with_retry(
    fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> T:
    """
    Call an async function and retry on HTTP 429/529 with exponential backoff + jitter.

    Args:
        fn:          An async callable (typically client.messages.create).
        *args:       Positional args forwarded to fn.
        max_retries: Maximum retry attempts.
        **kwargs:    Keyword args forwarded to fn.

    Raises:
        The last exception after all retries are exhausted.
    """
    attempt = 0
    delay = _BASE_DELAY_S

    while True:
        try:
            return await fn(*args, **kwargs)

        except Exception as exc:  # noqa: BLE001
            status_code = _extract_status_code(exc)

            if status_code not in _RETRYABLE_STATUS_CODES:
                raise

            attempt += 1
            if attempt > max_retries:
                logger.error(
                    "call_api_with_retry: exhausted %d retries (last status=%s): %s",
                    max_retries, status_code, exc,
                )
                raise

            sleep_for = _jitter(min(delay, _MAX_DELAY_S))
            logger.warning(
                "call_api_with_retry: status=%s attempt=%d/%d sleeping=%.2fs",
                status_code, attempt, max_retries, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay = min(delay * 2, _MAX_DELAY_S)


def _extract_status_code(exc: Exception) -> int | None:
    if hasattr(exc, "status_code"):
        return int(exc.status_code)
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def extract_json(text: str) -> str:
    """
    Extract the JSON substring from Claude's response text.

    Handles three formats:
      1. Raw JSON (the ideal case)
      2. ```json\\n...\\n``` markdown fences
      3. First '{' to last '}' heuristic

    Returns the best candidate string. Caller must still json.loads() it.
    Raises ValueError if no JSON-like content is found.
    """
    text = text.strip()

    # 1. Try as-is
    if text.startswith("{") and text.endswith("}"):
        return text

    # 2. Strip markdown fences
    import re
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    # 3. First '{' to last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    raise ValueError(f"No JSON object found in text: {text[:200]!r}")
