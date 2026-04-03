"""
shared/retry.py

Retry utilities and JSON extraction for Claude API calls.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, TypeVar

from anthropic import APIError, RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0


async def call_api_with_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    **kwargs: Any,
) -> T:
    """
    Call an async function with exponential backoff retry on transient errors.
    
    Args:
        func: Async function to call
        *args: Positional arguments for func
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        **kwargs: Keyword arguments for func
    
    Returns:
        Result of the function call
    
    Raises:
        The last exception if all retries are exhausted
    """
    last_error: Exception | None = None
    
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except RateLimitError as e:
            last_error = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    "Rate limited (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries + 1, delay, e
                )
                await asyncio.sleep(delay)
            else:
                logger.error("Rate limit exceeded after %d attempts", max_retries + 1)
        except APIError as e:
            last_error = e
            # Only retry on 5xx errors (server-side issues)
            if e.status_code and 500 <= e.status_code < 600 and attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    "API error %d (attempt %d/%d), retrying in %.1fs: %s",
                    e.status_code, attempt + 1, max_retries + 1, delay, e
                )
                await asyncio.sleep(delay)
            else:
                raise
    
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected retry loop exit")


def extract_json(text: str) -> str:
    """
    Extract JSON from Claude's response, handling markdown fences and other wrappers.
    
    Args:
        text: Raw response text that may contain JSON
    
    Returns:
        Extracted JSON string
    
    Raises:
        ValueError: If no valid JSON structure is found
    """
    if not text or not text.strip():
        raise ValueError("Empty input")
    
    text = text.strip()
    
    # Try extracting from markdown code fence first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate and (candidate.startswith("{") or candidate.startswith("[")):
            return candidate
    
    # If text itself looks like JSON, return it directly
    if text.startswith("{") or text.startswith("["):
        return text
    
    # Try to find a JSON object or array using robust bracket matching
    result = _extract_json_robust(text)
    if result:
        return result
    
    raise ValueError(f"No JSON found in text: {text[:100]}...")


def _extract_json_robust(text: str) -> str | None:
    """
    Extract JSON from text using proper bracket matching with escape handling.
    
    Handles:
    - Nested objects and arrays
    - Escaped quotes within strings
    - Mixed content before/after JSON
    
    Returns:
        Extracted JSON string or None if not found
    """
    # Find the first { or [
    start_obj = text.find("{")
    start_arr = text.find("[")
    
    if start_obj == -1 and start_arr == -1:
        return None
    
    # Determine which comes first
    if start_obj == -1:
        start = start_arr
        open_char, close_char = "[", "]"
    elif start_arr == -1:
        start = start_obj
        open_char, close_char = "{", "}"
    else:
        if start_obj < start_arr:
            start = start_obj
            open_char, close_char = "{", "}"
        else:
            start = start_arr
            open_char, close_char = "[", "]"
    
    # Track depth with proper string and escape handling
    depth = 0
    in_string = False
    i = start
    text_len = len(text)
    
    while i < text_len:
        char = text[i]
        
        # Handle escape sequences inside strings
        if in_string and char == "\\" and i + 1 < text_len:
            # Skip the next character (it's escaped)
            i += 2
            continue
        
        if char == '"':
            in_string = not in_string
        elif not in_string:
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        
        i += 1
    
    return None
