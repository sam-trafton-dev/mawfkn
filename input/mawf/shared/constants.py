"""
shared/constants.py — Single source of truth for all MAWF constants.

Both the orchestrator (Python) and obs-app (TypeScript via obs-app/src/lib/constants.ts)
read from the values defined here. Keep them in sync whenever you change either file.
"""

# ── Model ─────────────────────────────────────────────────────────────────────
MODEL = "claude-opus-4-5"  # No date suffix — exactly this string

# ── Token limits ──────────────────────────────────────────────────────────────
MAX_TOKENS_CODER: int    = 32000  # Coder needs the most — full file implementations
MAX_TOKENS_REVIEWER: int = 16000  # Reviewer critique can be long for large codebases
MAX_TOKENS_QA: int       = 16000  # QA test bench output
MAX_TOKENS_SME: int      = 4096   # SME answers are focused and concise
MAX_TOKENS_CHAT: int     = 1024   # Orchestrator chat parsing only

# ── Loop control ──────────────────────────────────────────────────────────────
MAX_ITERATIONS: int = 10        # Maximum agent loop iterations before forced termination
STUCK_HASH_WINDOW: int = 2      # Number of consecutive identical-output iterations = stuck
WAIT_TIMEOUT_S: int = 300       # Seconds to wait for an agent HTTP response (covers large Claude outputs)
HANG_TIMEOUT_S: int = 600       # Seconds before a hung iteration is killed

# ── Quality gates ─────────────────────────────────────────────────────────────
PASS_RATE_THRESHOLD: float = 0.90   # QA pass rate required for successful completion
MIN_PASS_RATE_EARLY: float = 0.50   # Minimum pass rate to continue the loop (below = escalate)

# ── Health polling ────────────────────────────────────────────────────────────
HEALTH_POLL_INTERVAL_S: int = 15    # Orchestrator polls /health every N seconds
HEALTH_MAX_RETRIES: int = 3         # Dead after this many consecutive failures

# ── Payload guard ─────────────────────────────────────────────────────────────
MAX_PAYLOAD_BYTES: int = 8 * 1024 * 1024  # 8 MB — larger payloads go to artifact volume

# ── SME cache ─────────────────────────────────────────────────────────────────
SME_PROMPT_VERSION: str = "v1"         # Bump to invalidate all SME cache entries
SME_CACHE_TTL_S: int = 3600            # 1 hour TTL for SME cache entries

# ── Agent loop sequence ───────────────────────────────────────────────────────
AGENT_SEQUENCE = ["coder", "reviewer", "qa"]  # Strictly sequential
