"""
services/orchestrator/health.py

Polls all agent /health endpoints every HEALTH_POLL_INTERVAL_S seconds.
If an agent fails HEALTH_MAX_RETRIES consecutive times it is declared dead:
  - An event is logged to the DB
  - The active session loop is terminated

Lock ordering (to prevent deadlock):
  1. _config_lock (threading.Lock) - guards AGENT_URLS registration
  2. _state_lock (asyncio.Lock) - guards _failure_counts, _dead_agents
  Never acquire _config_lock while holding _state_lock.

Note: _state_lock and _monitor_lock are lazily initialized on first use
to avoid creating asyncio.Lock outside of a running event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Coroutine

import httpx

from shared.constants import HEALTH_MAX_RETRIES, HEALTH_POLL_INTERVAL_S

logger = logging.getLogger(__name__)

# Lock ordering documentation:
# 1. _config_lock (threading.Lock) - for synchronous config changes
# 2. _state_lock (asyncio.Lock) - for async state mutations
# These must always be acquired in this order if both are needed.
_config_lock = threading.Lock()


class AgentHealthMonitor:
    """Background task that polls all registered agents and escalates on failure.
    
    Thread safety:
    - register_agents() is thread-safe via _config_lock
    - All async state access is protected by _state_lock
    - Callback invocation happens OUTSIDE of locks to prevent deadlock
    
    Lock ordering: _config_lock must be acquired before _state_lock if both needed.
    """

    def __init__(self) -> None:
        # Agent URL registry (protected by _config_lock)
        self._agent_urls: dict[str, str] = {}
        
        # Async state (protected by _state_lock, lazily created)
        self._state_lock: asyncio.Lock | None = None
        self._failure_counts: dict[str, int] = {}
        self._dead_agents: set[str] = set()
        
        # Runtime state
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        
        # Callback for termination (invoked OUTSIDE locks)
        self._terminate_callback: Callable[[str], Coroutine[Any, Any, None]] | None = None

    def _ensure_state_lock(self) -> asyncio.Lock:
        """Lazily create the state lock in the current event loop.
        
        This avoids the DeprecationWarning from creating asyncio.Lock()
        outside of a running event loop (Python 3.10+ issue).
        """
        if self._state_lock is None:
            self._state_lock = asyncio.Lock()
        return self._state_lock

    def register_agents(self, agent_urls: dict[str, str]) -> None:
        """Register agent name → base URL mapping.
        
        Thread-safe. Filters out SME agents (they have separate health semantics).
        """
        # Filter out SME agents - they're optional and have different health semantics
        filtered = {k: v for k, v in agent_urls.items() if not k.startswith("sme-")}
        
        if not filtered:
            logger.warning("No non-SME agents to register for health monitoring")
            return
        
        with _config_lock:
            self._agent_urls.update(filtered)
            # Initialize failure counts for new agents
            for name in filtered:
                if name not in self._failure_counts:
                    self._failure_counts[name] = 0
        
        logger.info("Registered %d agents for health monitoring: %s", 
                    len(filtered), list(filtered.keys()))

    def set_terminate_callback(
        self, 
        callback: Callable[[str], Coroutine[Any, Any, None]]
    ) -> None:
        """Set the async callback to invoke when an agent is declared dead.
        
        The callback receives a reason string and should terminate active loops.
        It is invoked OUTSIDE of any locks to prevent deadlock.
        """
        self._terminate_callback = callback

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            logger.warning("Health monitor already running")
            return
        
        # Ensure lock exists in this event loop
        self._ensure_state_lock()
        
        self._running = True
        self._client = httpx.AsyncClient(timeout=5.0)
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
        self._task = None
        
        if self._client:
            await self._client.aclose()
            self._client = None
        
        logger.info("AgentHealthMonitor stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop - checks all agents periodically."""
        while self._running:
            # Snapshot agent URLs under config lock (fast, synchronous)
            with _config_lock:
                agents_snapshot = dict(self._agent_urls)
            
            if not agents_snapshot:
                await asyncio.sleep(HEALTH_POLL_INTERVAL_S)
                continue
            
            # Check all agents concurrently
            client = self._client
            if client is None:
                break
            
            await asyncio.gather(
                *[self._check_agent(client, name, url) 
                  for name, url in agents_snapshot.items()],
                return_exceptions=True,
            )
            await asyncio.sleep(HEALTH_POLL_INTERVAL_S)

    async def _check_agent(
        self, 
        client: httpx.AsyncClient, 
        name: str, 
        base_url: str
    ) -> None:
        """Check a single agent's health endpoint."""
        url = f"{base_url.rstrip('/')}/health"
        lock = self._ensure_state_lock()
        
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            
            # Success - reset failure count and potentially resurrect
            async with lock:
                old_count = self._failure_counts.get(name, 0)
                self._failure_counts[name] = 0
                
                # Agent recovery: remove from dead set if it was there
                was_dead = name in self._dead_agents
                if was_dead:
                    self._dead_agents.discard(name)
            
            if old_count > 0:
                logger.info("Agent '%s' recovered (was at %d failures)", name, old_count)
            if was_dead:
                logger.info("Agent '%s' resurrected after being declared dead", name)

        except Exception as exc:  # noqa: BLE001
            # Failure - increment count under lock, then check if dead
            should_escalate = False
            async with lock:
                self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
                count = self._failure_counts[name]
                
                if count >= HEALTH_MAX_RETRIES and name not in self._dead_agents:
                    self._dead_agents.add(name)
                    should_escalate = True
            
            logger.warning(
                "Agent '%s' health check failed (%d/%d): %s",
                name, count, HEALTH_MAX_RETRIES, exc,
            )
            
            # Escalate OUTSIDE of lock to prevent deadlock
            if should_escalate:
                logger.error(
                    "Agent '%s' declared DEAD after %d consecutive failures", 
                    name, HEALTH_MAX_RETRIES
                )
                await self._escalate_dead_agent(name)

    async def _escalate_dead_agent(self, agent_name: str) -> None:
        """Called when an agent exceeds the max retry threshold.
        
        Invokes the terminate callback OUTSIDE of any locks.
        """
        reason = f"Agent '{agent_name}' is unresponsive after {HEALTH_MAX_RETRIES} retries"
        
        if self._terminate_callback is not None:
            try:
                await self._terminate_callback(reason)
            except Exception as exc:  # noqa: BLE001
                logger.error("Terminate callback failed for dead agent '%s': %s", 
                             agent_name, exc)
        else:
            logger.warning("No terminate callback registered — cannot terminate loops for: %s", reason)

    async def is_agent_dead(self, agent_name: str) -> bool:
        """Check if an agent has been declared dead.
        
        Note: This is an async method to ensure thread-safe access to _dead_agents.
        """
        lock = self._ensure_state_lock()
        async with lock:
            return agent_name in self._dead_agents

    async def get_agent_status(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of all agent health status.
        
        Returns dict mapping agent name to:
            {"failures": int, "dead": bool, "url": str}
        """
        lock = self._ensure_state_lock()
        async with lock:
            failure_snapshot = dict(self._failure_counts)
            dead_snapshot = set(self._dead_agents)
        
        with _config_lock:
            urls_snapshot = dict(self._agent_urls)
        
        result = {}
        for name, url in urls_snapshot.items():
            result[name] = {
                "failures": failure_snapshot.get(name, 0),
                "dead": name in dead_snapshot,
                "url": url,
            }
        return result

    def clear_registrations(self) -> None:
        """Clear all registered agents and reset state.
        
        Used for test cleanup. Thread-safe.
        """
        with _config_lock:
            self._agent_urls.clear()
            self._failure_counts.clear()
            self._dead_agents.clear()


# Module-level singleton (lazily initialized)
_monitor: AgentHealthMonitor | None = None
_monitor_lock: asyncio.Lock | None = None


def _ensure_monitor_lock() -> asyncio.Lock:
    """Lazily create the monitor lock in the current event loop."""
    global _monitor_lock
    if _monitor_lock is None:
        _monitor_lock = asyncio.Lock()
    return _monitor_lock


async def get_monitor() -> AgentHealthMonitor:
    """Get or create the singleton AgentHealthMonitor.
    
    Must be called from within an async context (running event loop).
    """
    global _monitor
    
    lock = _ensure_monitor_lock()
    async with lock:
        if _monitor is None:
            _monitor = AgentHealthMonitor()
        return _monitor


def get_monitor_sync() -> AgentHealthMonitor:
    """Get the singleton monitor synchronously.
    
    Raises RuntimeError if monitor hasn't been initialized via get_monitor() yet.
    Use this only in contexts where you know the monitor was already created.
    """
    if _monitor is None:
        raise RuntimeError(
            "Health monitor not initialized. Call 'await get_monitor()' first."
        )
    return _monitor


async def reset_monitor() -> None:
    """Reset the singleton monitor (for testing).
    
    Stops the monitor if running and clears all state including AGENT_URLS.
    """
    global _monitor, _monitor_lock
    
    if _monitor is not None:
        await _monitor.stop()
        _monitor.clear_registrations()
        _monitor = None
    
    _monitor_lock = None
