"""
shared/constants.py

Single source of truth for all tunable parameters across agents.
Keep these simple integers/floats that can be imported everywhere.

These constants are also mirrored in obs-app/src/lib/constants.ts for the frontend.
Keep both files in sync when making changes.

Used by:
- Orchestrator loop (MAX_ITERATIONS, STUCK_HASH_WINDOW, HANG_TIMEOUT_S, etc.)
- Health monitor (HEALTH_POLL_INTERVAL_S, HEALTH_MAX_RETRIES)
- All agents (MODEL, MAX_TOKENS_*)
- QA agent (PASS_RATE_THRESHOLD, MIN_PASS_RATE_EARLY)
- Coder agent (MAX_PAYLOAD_BYTES)
- Frontend obs-app (mirrored in constants.ts)
"""

from __future__ import annotations

# ── Agent loop control ────────────────────────────────────────────────────────

# Maximum iterations before the loop is terminated
MAX_ITERATIONS: int = 10

# Number of consecutive identical-output iterations that trigger stuck detection
STUCK_HASH_WINDOW: int = 2

# Seconds before a hung iteration is killed
HANG_TIMEOUT_S: int = 600

# Per-agent call timeout in seconds (used by orchestrator when calling agent /run endpoints)
WAIT_TIMEOUT_S: int = 300

# ── Quality gates ─────────────────────────────────────────────────────────────

# QA pass rate required for successful loop completion
PASS_RATE_THRESHOLD: float = 0.90

# Minimum pass rate to continue the loop (below this after iteration 5 = warning)
MIN_PASS_RATE_EARLY: float = 0.50

# ── Model configuration ───────────────────────────────────────────────────────

# Anthropic model identifier (no date suffix per API best practices)
MODEL: str = "claude-sonnet-4-20250514"

# Token limits by agent type
MAX_TOKENS_CODER: int = 16384
MAX_TOKENS_REVIEWER: int = 4096
MAX_TOKENS_QA: int = 4096
MAX_TOKENS_SME: int = 4096
MAX_TOKENS_CHAT: int = 2048

# ── Health monitoring ─────────────────────────────────────────────────────────

# Orchestrator polls agent /health endpoints every N seconds
HEALTH_POLL_INTERVAL_S: int = 15

# Dead agent threshold: consecutive failures before escalation
HEALTH_MAX_RETRIES: int = 3

# ── Payload size limits ───────────────────────────────────────────────────────

# Maximum payload size before offloading to artifact volume (8 MB)
MAX_PAYLOAD_BYTES: int = 8 * 1024 * 1024

# ── Retry configuration ───────────────────────────────────────────────────────

# API call retry settings (used by shared.retry.call_api_with_retry)
RETRY_MAX_ATTEMPTS: int = 3
RETRY_BASE_DELAY_S: float = 1.0
RETRY_MAX_DELAY_S: float = 30.0
RETRY_EXPONENTIAL_BASE: float = 2.0
