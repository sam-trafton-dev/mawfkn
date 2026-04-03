/**
 * obs-app/src/lib/constants.ts
 *
 * TypeScript mirror of shared/constants.py.
 * Keep these values in sync with the Python source of truth.
 *
 * These are deliberately typed as const so they are inlined at build time
 * and available in both server and client components.
 */

export const CONSTANTS = {
  /** Maximum agent loop iterations before forced termination */
  MAX_ITERATIONS: 10,

  /** Number of consecutive identical-output iterations that trigger stuck detection */
  STUCK_HASH_WINDOW: 2,

  /** Seconds before a hung iteration is killed */
  HANG_TIMEOUT_S: 600,

  /** QA pass rate required for successful loop completion */
  PASS_RATE_THRESHOLD: 0.90,

  /** Minimum pass rate to continue the loop (below = escalate) */
  MIN_PASS_RATE_EARLY: 0.50,

  /** Model identifier — no date suffix */
  MODEL: 'claude-opus-4-5',

  /** Orchestrator polls /health every N seconds */
  HEALTH_POLL_INTERVAL_S: 15,

  /** Dead agent threshold: consecutive failures before escalation */
  HEALTH_MAX_RETRIES: 3,

  /** Payload size limit before offloading to artifact volume (bytes) */
  MAX_PAYLOAD_BYTES: 8 * 1024 * 1024,
} as const;

export type ConstantKey = keyof typeof CONSTANTS;

/** Human-readable labels for session status values */
export const STATUS_LABELS: Record<string, string> = {
  pending:   'Pending',
  running:   'Running',
  completed: 'Completed',
  failed:    'Failed',
  stuck:     'Stuck',
};

/** Tailwind colour tokens for each session status (must match tailwind.config.ts) */
export const STATUS_COLORS: Record<string, string> = {
  pending:   'text-yellow-400',
  running:   'text-blue-400',
  completed: 'text-emerald-400',
  failed:    'text-red-400',
  stuck:     'text-purple-400',
};

/** Strictly sequential agent loop order */
export const AGENT_SEQUENCE = ['coder', 'reviewer', 'qa'] as const;
export type AgentRole = typeof AGENT_SEQUENCE[number] | 'orchestrator';

/** Base URL for the orchestrator — read from env at runtime */
export function getOrchestratorUrl(): string {
  return (
    process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? 'http://localhost:8000'
  );
}

/** Base WebSocket URL for real-time event streaming */
export function getWsUrl(): string {
  return (
    process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8000'
  );
}
