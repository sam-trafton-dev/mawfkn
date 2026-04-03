"""
services/orchestrator/health.py

Polls all agent /health endpoints every HEALTH_POLL_INTERVAL_S seconds.
If an agent fails HEALTH_MAX_RETRIES consecutive times it is declared dead:
  - An event is logged to the DB
  - The active session loop is terminated
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from shared.constants import HEALTH_MAX_RETRIES, HEALTH_POLL_INTERVAL_S

logger = logging.getLogger(__name__)

# Registry populated at startup from environment variables
AGENT_URLS: dict[str, str] = {}


class AgentHealthMonitor:
    """Background task that polls all registered agents and escalates on failure."""

    def __init__(self, loop_controller: Any | None = None) -> None:
        """
        Args:
            loop_controller: An object with a `.terminate(reason: str)` coroutine
                             that the health monitor calls when an agent dies.
        """
        self._loop_controller = loop_controller
        self._failure_counts: dict[str, int] = {}
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    def register_agents(self, agent_urls: dict[str, str]) -> None:
        """Register agent name → base URL mapping."""
        AGENT_URLS.update(agent_urls)
        for name in agent_urls:
            self._failure_counts.setdefault(name, 0)

    async def start(self) -> None:
        """Start the background polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="health-monitor")
        logger.info("AgentHealthMonitor started — polling every %ds", HEALTH_POLL_INTERVAL_S)

    async def stop(self) -> None:
        """Cancel the background polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AgentHealthMonitor stopped")

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            while self._running:
                await asyncio.gather(
                    *[self._check_agent(client, name, url) for name, url in AGENT_URLS.items()],
                    return_exceptions=True,
                )
                await asyncio.sleep(HEALTH_POLL_INTERVAL_S)

    async def _check_agent(self, client: httpx.AsyncClient, name: str, base_url: str) -> None:
        url = f"{base_url.rstrip('/')}/health"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            # Reset consecutive failure count on success
            if self._failure_counts.get(name, 0) > 0:
                logger.info("Agent '%s' recovered (was failing)", name)
            self._failure_counts[name] = 0

        except Exception as exc:  # noqa: BLE001
            self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
            count = self._failure_counts[name]
            logger.warning(
                "Agent '%s' health check failed (%d/%d): %s",
                name, count, HEALTH_MAX_RETRIES, exc,
            )

            if count >= HEALTH_MAX_RETRIES:
                logger.error("Agent '%s' declared DEAD after %d consecutive failures", name, count)
                await self._escalate_dead_agent(name)

    async def _escalate_dead_agent(self, agent_name: str) -> None:
        """Called when an agent exceeds the max retry threshold."""
        reason = f"Agent '{agent_name}' is unresponsive after {HEALTH_MAX_RETRIES} retries"

        if self._loop_controller is not None:
            try:
                await self._loop_controller.terminate(reason=reason)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to terminate loop after dead agent: %s", exc)
        else:
            logger.error("No loop_controller registered — cannot terminate loop: %s", reason)


# Module-level singleton
_monitor: AgentHealthMonitor | None = None


def get_monitor() -> AgentHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = AgentHealthMonitor()
    return _monitor
