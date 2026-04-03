"""
services/orchestrator/loop.py

The core agent loop controller.

Loop sequence (strictly sequential per spec):
    Coder → Reviewer → QA → Orchestrator evaluates

Constants (from shared.constants — do NOT redefine locally):
    WAIT_TIMEOUT_S      = 30
    HANG_TIMEOUT_S      = 600
    MAX_ITERATIONS      = 10
    STUCK_HASH_WINDOW   = 2
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import redis.asyncio as aioredis

from shared.constants import (
    HANG_TIMEOUT_S,
    MAX_ITERATIONS,
    MIN_PASS_RATE_EARLY,
    PASS_RATE_THRESHOLD,
    STUCK_HASH_WINDOW,
    WAIT_TIMEOUT_S,
)
from shared.db import log_event, update_session_status, write_iteration
from shared.redis_client import events_channel, publish, push_dead_letter
from shared.retry import call_api_with_retry  # noqa: F401 — available for orchestrator LLM calls

logger = logging.getLogger(__name__)

# ── Service URLs (resolved from env at import time) ────────────────────────────
CODER_URL    = os.getenv("CODER_URL",    "http://coder:8001")
REVIEWER_URL = os.getenv("REVIEWER_URL", "http://reviewer:8002")
QA_URL       = os.getenv("QA_URL",       "http://qa:8003")
ARTIFACT_PATH = Path(os.getenv("ARTIFACT_PATH", "/artifacts"))


def _hash_output(output: dict[str, Any]) -> str:
    """Stable SHA-256 of a JSON-serialisable dict, used for stuck detection."""
    serialised = json.dumps(output, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()


def _is_stuck(window: deque[str]) -> bool:
    """Return True if the last STUCK_HASH_WINDOW hashes are all identical."""
    if len(window) < STUCK_HASH_WINDOW:
        return False
    last_n = list(window)[-STUCK_HASH_WINDOW:]
    return len(set(last_n)) == 1


class LoopController:
    """
    Manages a single session's agent loop.

    Usage::

        controller = LoopController(session_id=sid, task_spec=spec, pool=pool, redis=redis)
        await controller.run()
    """

    def __init__(
        self,
        session_id: str,
        task_spec: dict[str, Any],
        pool: asyncpg.Pool,
        redis: aioredis.Redis,  # type: ignore[type-arg]
        resume_from: list[dict[str, Any]] | None = None,
    ) -> None:
        self.session_id = session_id
        self.task_spec = task_spec
        self._pool = pool
        self._redis = redis

        self._terminated = False
        self._terminate_reason: str | None = None
        self._output_hashes: deque[str] = deque(maxlen=STUCK_HASH_WINDOW + 1)

        # When resuming, seed _previous_outputs with the last iteration's outputs
        # so the coder receives full prior context on the first new iteration.
        if resume_from:
            last = resume_from[-1]
            raw = last.get("outputs") or last
            # asyncpg may return jsonb as a string in some configurations — parse defensively
            if isinstance(raw, str):
                raw = json.loads(raw)
            self._previous_outputs: dict[str, Any] | None = raw if isinstance(raw, dict) else None
        else:
            self._previous_outputs = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """
        Execute the full agent loop and return a summary dict.

        Returns:
            {
                "session_id": str,
                "iterations_run": int,
                "final_pass_rate": float | None,
                "status": "completed" | "failed" | "stuck" | "terminated",
                "reason": str,
            }
        """
        logger.info("Loop starting — session=%s max_iterations=%d", self.session_id, MAX_ITERATIONS)
        await update_session_status(self._pool, self.session_id, "running")

        final_pass_rate: float | None = None
        status = "failed"
        reason = "unknown"
        loop_n = 0

        for loop_n in range(1, MAX_ITERATIONS + 1):
            if self._terminated:
                status = "terminated"
                reason = self._terminate_reason or "externally terminated"
                break

            logger.info("=== Iteration %d/%d ===", loop_n, MAX_ITERATIONS)
            await self._emit_event("orchestrator", "iteration_start", {"loop_n": loop_n})

            try:
                iteration_result = await asyncio.wait_for(
                    self._run_iteration(loop_n, self._previous_outputs),
                    timeout=HANG_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                reason = f"Iteration {loop_n} hung for {HANG_TIMEOUT_S}s — escalating"
                logger.error(reason)
                await push_dead_letter(
                    self._redis,
                    self.session_id,
                    reason=reason,
                    original_message={"loop_n": loop_n, "session_id": self.session_id},
                )
                await self._emit_event("orchestrator", "hang_timeout", {"loop_n": loop_n, "reason": reason})
                status = "failed"
                break
            except Exception as exc:  # noqa: BLE001
                exc_repr = repr(exc) or type(exc).__name__
                reason = f"Iteration {loop_n} raised: {exc_repr}"
                logger.exception(reason)
                await self._emit_event("orchestrator", "iteration_error", {
                    "loop_n": loop_n,
                    "error": exc_repr,
                    "type": type(exc).__name__,
                })
                status = "failed"
                break

            final_pass_rate = iteration_result.get("qa", {}).get("pass_rate")
            output_hash = _hash_output(iteration_result)
            self._output_hashes.append(output_hash)

            self._previous_outputs = iteration_result
            await write_iteration(self._pool, self.session_id, loop_n, iteration_result, final_pass_rate)
            await self._emit_event("orchestrator", "iteration_end", {
                "loop_n": loop_n,
                "pass_rate": final_pass_rate,
                "output_hash": output_hash,
            })

            # ── Evaluate quality gate ──────────────────────────────────────────
            if final_pass_rate is not None:
                if final_pass_rate >= PASS_RATE_THRESHOLD:
                    status = "completed"
                    reason = f"Pass rate {final_pass_rate:.2%} >= threshold {PASS_RATE_THRESHOLD:.2%}"
                    logger.info("Loop completed successfully: %s", reason)
                    break

                if final_pass_rate < MIN_PASS_RATE_EARLY and loop_n > 5:
                    logger.warning(
                        "Pass rate %.2f below early-exit threshold %.2f after iteration %d",
                        final_pass_rate, MIN_PASS_RATE_EARLY, loop_n,
                    )
                    await self._emit_event("orchestrator", "low_pass_rate_alert", {
                        "loop_n": loop_n,
                        "pass_rate": final_pass_rate,
                    })

            # ── Stuck detection ───────────────────────────────────────────────
            if _is_stuck(self._output_hashes):
                status = "stuck"
                reason = f"Outputs unchanged for {STUCK_HASH_WINDOW} consecutive iterations"
                logger.warning("Loop stuck: %s", reason)
                await self._emit_event("orchestrator", "stuck_detected", {"loop_n": loop_n, "reason": reason})
                break

        else:
            # Exhausted all iterations without breaking
            status = "failed"
            reason = f"Max iterations ({MAX_ITERATIONS}) reached without meeting pass-rate threshold"

        await update_session_status(self._pool, self.session_id, status)
        await self._emit_event("orchestrator", "loop_end", {
            "status": status,
            "reason": reason,
            "iterations_run": loop_n,
            "final_pass_rate": final_pass_rate,
        })
        logger.info("Loop ended — session=%s status=%s reason=%s", self.session_id, status, reason)

        return {
            "session_id": self.session_id,
            "iterations_run": loop_n,
            "final_pass_rate": final_pass_rate,
            "status": status,
            "reason": reason,
        }

    async def terminate(self, reason: str = "requested") -> None:
        """Signal the loop to stop after the current iteration completes."""
        logger.warning("Loop terminate requested: %s", reason)
        self._terminated = True
        self._terminate_reason = reason

    # ── Private: iteration steps ───────────────────────────────────────────────

    async def _run_iteration(
        self, loop_n: int, previous_outputs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run one full Coder → Reviewer → QA cycle."""
        outputs: dict[str, Any] = {}

        # Step 1: Coder — include previous reviewer/QA feedback on iterations > 1
        coder_payload: dict[str, Any] = {
            "session_id": self.session_id,
            "loop_n": loop_n,
            "task_spec": self.task_spec,
        }
        if previous_outputs:
            if isinstance(previous_outputs, str):
                previous_outputs = json.loads(previous_outputs)
            if isinstance(previous_outputs, dict):
                coder_payload["reviewer_feedback"] = previous_outputs.get("reviewer")
                coder_payload["qa_feedback"] = previous_outputs.get("qa")

        coder_output = await self._call_agent(
            agent="coder",
            url=f"{CODER_URL}/run",
            payload=coder_payload,
        )
        outputs["coder"] = coder_output

        # Materialize coder files before QA so real tests can run against them
        await self._materialize_files(loop_n, {"coder": coder_output})

        # Step 2: Reviewer (receives coder output)
        reviewer_output = await self._call_agent(
            agent="reviewer",
            url=f"{REVIEWER_URL}/run",
            payload={
                "session_id": self.session_id,
                "loop_n": loop_n,
                "coder_output": coder_output,
            },
        )
        outputs["reviewer"] = reviewer_output

        # Step 3: QA (receives coder + reviewer output)
        qa_output = await self._call_agent(
            agent="qa",
            url=f"{QA_URL}/run",
            payload={
                "session_id": self.session_id,
                "loop_n": loop_n,
                "coder_output": coder_output,
                "reviewer_output": reviewer_output,
                "artifact_name": self.task_spec.get("artifact_name"),
                "input_path": self.task_spec.get("input_path"),
            },
        )
        outputs["qa"] = qa_output

        return outputs

    async def _call_agent(
        self,
        agent: str,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        POST to an agent /run endpoint with a per-call timeout.
        The HANG_TIMEOUT_S outer guard covers the full iteration;
        WAIT_TIMEOUT_S is the per-agent call limit.
        """
        await self._emit_event("orchestrator", f"{agent}_assigned", {"loop_n": payload.get("loop_n")})
        try:
            async with httpx.AsyncClient(timeout=WAIT_TIMEOUT_S) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()
        except httpx.TimeoutException as exc:
            reason = f"{agent} did not respond within {WAIT_TIMEOUT_S}s"
            await push_dead_letter(
                self._redis,
                self.session_id,
                reason=reason,
                original_message=payload,
            )
            await self._emit_event("orchestrator", f"{agent}_timeout", {
                "loop_n": payload.get("loop_n"),
                "error": reason,
                "type": type(exc).__name__,
            })
            raise
        except httpx.HTTPStatusError as exc:
            await self._emit_event("orchestrator", f"{agent}_error", {
                "status_code": exc.response.status_code,
                "detail": exc.response.text[:500],
            })
            raise

        # Build a rich summary payload for the obs-app group chat UI
        loop_n = payload.get("loop_n")
        chat_payload: dict[str, Any] = {"loop_n": loop_n, "result_keys": list(result.keys())}
        if agent == "coder":
            chat_payload["summary"] = result.get("summary") or result.get("notes") or ""
            chat_payload["file_count"] = len(result.get("files", {}))
        elif agent == "reviewer":
            chat_payload["summary"] = result.get("summary", "")
            chat_payload["critical_count"] = len(result.get("critical", []))
            chat_payload["major_count"] = len(result.get("major", []))
            chat_payload["minor_count"] = len(result.get("minor", []))
        elif agent == "qa":
            chat_payload["summary"] = result.get("notes", "")
            chat_payload["pass_rate"] = result.get("pass_rate")
            chat_payload["passed_count"] = len(result.get("passed", []))
            chat_payload["failed_count"] = len(result.get("failed", []))

        await self._emit_event("orchestrator", f"{agent}_result_received", chat_payload)
        return result

    # ── Private: file materialization ──────────────────────────────────────────

    async def _materialize_files(self, loop_n: int, iteration_result: dict[str, Any]) -> None:
        """Write coder-produced files to the artifact volume on the host."""
        files: dict[str, str] = iteration_result.get("coder", {}).get("files", {})
        if not files:
            return
        folder_name = self.task_spec.get("artifact_name") or self.session_id
        src_root = ARTIFACT_PATH / folder_name / "src"
        try:
            await asyncio.to_thread(self._write_files_sync, src_root, files)
            logger.info("Materialized %d file(s) → %s (loop %d)", len(files), src_root, loop_n)
        except Exception as exc:  # noqa: BLE001
            logger.error("File materialization failed (non-fatal): %s", exc)

    @staticmethod
    def _write_files_sync(src_root: Path, files: dict[str, str]) -> None:
        """Synchronous file write — called via asyncio.to_thread."""
        for rel_path, content in files.items():
            dest = src_root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

    # ── Private: event helpers ─────────────────────────────────────────────────

    async def _emit_event(
        self,
        agent_role: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Write to postgres events table and publish to Redis for real-time streaming."""
        # Postgres (durable)
        try:
            await log_event(self._pool, self.session_id, agent_role, event_type, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to write event to DB: %s", exc)

        # Redis pub/sub (real-time obs-app feed)
        try:
            await publish(self._redis, events_channel(self.session_id), {
                "agent_role": agent_role,
                "event_type": event_type,
                "payload": payload,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis publish failed (non-fatal): %s", exc)
