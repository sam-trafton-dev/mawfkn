"""
services/coder/tools.py

Utility functions for the coder agent.

Key export:
    guard_payload(payload, artifact_path, session_id, loop_n, redis_client)
        If the payload exceeds 8 MB, writes the full diff to the artifact volume
        and returns a lightweight reference dict instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import aiofiles

from shared.constants import MAX_PAYLOAD_BYTES

logger = logging.getLogger(__name__)

ARTIFACT_PATH = Path(os.getenv("ARTIFACT_PATH", "/artifacts"))


async def guard_payload(
    payload: dict[str, Any],
    session_id: str,
    loop_n: int,
    redis_client: Any | None = None,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    """
    If the serialised payload exceeds MAX_PAYLOAD_BYTES (8 MB), write the full
    content to the artifact volume and return a lightweight reference dict
    containing only the artifact path and a one-line summary.

    The reference dict is safe to publish via Redis or return in an API response.

    Args:
        payload:       The dict to potentially offload.
        session_id:    Used to namespace the artifact file.
        loop_n:        Current iteration number (used in filename).
        redis_client:  Optional Redis client; if provided the reference is also
                       published to ``artifacts:{session_id}`` channel.
        artifact_path: Override the artifact root (default: ARTIFACT_PATH env var).

    Returns:
        Either the original payload (if small enough) or a reference dict.
    """
    root = artifact_path or ARTIFACT_PATH
    serialised = json.dumps(payload, default=str).encode()

    if len(serialised) <= MAX_PAYLOAD_BYTES:
        return payload

    # Write to artifact volume
    root.mkdir(parents=True, exist_ok=True)
    artifact_id = str(uuid.uuid4())
    artifact_file = root / session_id / f"loop_{loop_n:04d}_{artifact_id}.json"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(artifact_file, "wb") as fh:
        await fh.write(serialised)

    size_mb = len(serialised) / (1024 * 1024)
    summary = (
        f"Payload offloaded to artifact volume ({size_mb:.2f} MB). "
        f"Keys: {list(payload.keys())[:10]}"
    )
    logger.info("guard_payload: offloaded %.2f MB → %s", size_mb, artifact_file)

    reference = {
        "__artifact__": True,
        "artifact_path": str(artifact_file),
        "size_bytes": len(serialised),
        "summary": summary,
        "session_id": session_id,
        "loop_n": loop_n,
    }

    # Publish the reference path to Redis for other services to consume
    if redis_client is not None:
        try:
            channel = f"artifacts:{session_id}"
            await redis_client.publish(channel, json.dumps(reference))
        except Exception as exc:  # noqa: BLE001
            logger.warning("guard_payload: Redis publish failed: %s", exc)

    return reference


async def load_artifact(reference: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve an artifact reference back to the original payload.

    Args:
        reference: A dict returned by guard_payload (must have ``__artifact__`` key).

    Returns:
        The original payload dict.
    """
    if not reference.get("__artifact__"):
        return reference

    artifact_file = Path(reference["artifact_path"])
    if not artifact_file.exists():
        raise FileNotFoundError(f"Artifact not found: {artifact_file}")

    async with aiofiles.open(artifact_file, "rb") as fh:
        data = await fh.read()

    return json.loads(data)


def compute_diff_hash(content: str) -> str:
    """Return a SHA-256 hex digest of the given string (for change detection)."""
    return hashlib.sha256(content.encode()).hexdigest()
