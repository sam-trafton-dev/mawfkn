# Workshop Template — Claude Code Context

## What This Is

A containerized multi-agent software development system. Each "workshop" is a Docker Compose stack: one orchestrator, three core loop agents (coder, reviewer, QA), six SME knowledge agents, a PostgreSQL context store, a Redis message bus, and a Next.js observability app. Multiple workshops run concurrently on the same host with fully isolated state.

---

## Architecture — Read Before Touching Anything
Refer to the files in ./implement/ directory for source of truth

### Agent execution is strictly sequential, not parallel

```
User → Orchestrator → Coder → Reviewer → QA → Orchestrator evaluates → repeat
```

The orchestrator assigns, waits, then assigns the next agent. There is no concurrent agent execution within a loop iteration. Do not introduce parallelism in `loop.py` without explicit instruction.

### Core agents

| Service | Port | Role |
|---|---|---|
| orchestrator | 8000 | Entry point, task decomposition, loop control, stuck detection |
| coder | 8001 | Feature implementation, writes to artifact volume |
| reviewer | 8002 | Adversarial critique — assumes bugs exist, outputs structured JSON |
| qa | 8003 | Builds and runs test bench, reports pass rate |

### SME agents (stateless, port 8080 each)

`sme-data`, `sme-api`, `sme-ux`, `sme-business`, `sme-networking`, `sme-devops`

SMEs are called by core agents via HTTP. They do not write to the context store. Responses are cached in the `sme_cache` PostgreSQL table. Do not add state to SME agents.

### Infrastructure

- **PostgreSQL** — context store (agent identity, task state, session history, SME cache)
- **Redis** — pub/sub message bus between agents; dead-letter queue enabled; 8 MB message size cap
- **Artifact volume** — shared between orchestrator, coder, reviewer, and QA only

---

## Hard rules (do not violate)

- **Verify with user before assuming any design choices**
- **Simple is better** Any system that doesn't require complexity should not have it

## Model

All agents use: `claude-opus-4-5`

The `.env` default is `LLM_MODEL=claude-opus-4-5`. Do not use model strings with date suffixes — they will 404.

---

## Critical Implementation Rules

### loop.py — never remove these guards

```python
WAIT_TIMEOUT_S    = 30    # every wait_for() call has this timeout
HANG_TIMEOUT_S    = 600   # no event for 10 min = stuck, escalate to user
MAX_ITERATIONS    = 10
STUCK_HASH_WINDOW = 2     # flag if output hash unchanged for N iterations
```

On timeout: push to dead-letter queue, notify user, return. Never let `wait_for()` block indefinitely.

### API calls — always go through retry.py

All Anthropic API calls must use `call_api_with_retry()` from `services/orchestrator/retry.py`. This handles 429 and 529 with exponential backoff and jitter. Do not call `client.messages.create()` directly in agent code.

### SME agents — no shared client state

```python
# CORRECT
client = anthropic.AsyncAnthropic()
result = await client.messages.create(...)

# WRONG — do not instantiate at module level and reuse
client = Anthropic()  # module-level shared client — breaks under concurrent requests
```

### SME cache key must include prompt version

```python
PROMPT_VERSION = hashlib.sha256(PROMPTS[DOMAIN].encode()).hexdigest()[:16]
q_hash = hashlib.sha256(f'{PROMPT_VERSION}{session_id}{DOMAIN}{question}'.encode()).hexdigest()
```

If you change a SME system prompt, the `PROMPT_VERSION` hash changes automatically and the old cache entries are ignored. Cache TTL is 24h via `expires_at` column.

### Large code payloads — use guard_payload()

Before publishing coder output to Redis, call `guard_payload()` from `services/coder/tools.py`. If the payload exceeds 8 MB, the diff is written to the artifact volume and only the path + summary are sent via Redis. The reviewer reads from the artifact volume when `diff_path` is present.

### Health checks — all agents must expose /health

```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```

The orchestrator polls all agents every 15s via `services/orchestrator/health.py`. A dead agent (3 failed retries) triggers immediate user escalation and loop termination. Do not remove health endpoints.

---

## Database Schema (PostgreSQL)

Key tables in `db/migrations/001_init.sql`:

- `sessions` — one row per user task
- `agent_states` — persisted system prompts and state blobs per agent per session
- `iterations` — one row per loop iteration with outputs and QA pass rate
- `events` — append-only event log (all agent actions)
- `sme_cache` — cached SME responses with `prompt_version`, `query_hash`, `expires_at`

Do not add new tables without a migration file in `db/migrations/`. Do not modify `sme_cache` without updating the cache key logic in `base_sme.py` and `tools.py`.

---

## Docker Compose Conventions

### Resource limits are required on all agent services

```yaml
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 1G
```

Do not remove these. Multiple workshops share the host; without limits one workshop can starve others.

### New workshops use port offsets of 10

| Workshop | Orchestrator | Coder | Reviewer | QA | Obs App |
|---|---|---|---|---|---|
| A | 8000 | 8001 | 8002 | 8003 | 3000 |
| B | 8010 | 8011 | 8012 | 8013 | 3001 |
| C | 8020 | 8021 | 8022 | 8023 | 3002 |

Use `./scripts/new_workshop.sh <name> <offset>` to spin up a new workshop. Do not manually copy `.env` files.

### Depends_on uses service_healthy for postgres and redis

```yaml
depends_on:
  postgres: { condition: service_healthy }
  redis: { condition: service_healthy }
```

Both services have healthchecks defined. Do not downgrade to `service_started`.

### PostgreSQL and Redis are not exposed to the host

No `ports:` binding on `postgres` or `redis` services. Keep it that way.

### Artifact volume is shared only between orchestrator, coder, reviewer, and QA

Do not mount the artifact volume into SME services or the obs app.

---

## Reviewer Output Format

The reviewer always outputs this JSON structure. Do not change the schema — the orchestrator and obs app parse it:

```json
{
  "critical": [],
  "major": [],
  "minor": [],
  "summary": "one sentence assessment"
}
```

Exit condition: reviewer critique length trend decreasing over last 3 iterations is one of the loop exit signals. The orchestrator tracks this.

## QA Output Format

```json
{
  "pass_rate": 0.87,
  "passed": [],
  "failed": [],
  "coverage_delta": 0.04,
  "notes": ""
}
```

Default exit threshold: `pass_rate >= 0.90`. Configured in `shared/constants.py`.

---

## Shared Constants

`shared/constants.py` is the single source of truth for all thresholds. Both the orchestrator and obs app import from here. Do not hardcode these values elsewhere:

```python
MAX_ITERATIONS      = 10
STUCK_HASH_WINDOW   = 2
HANG_TIMEOUT_S      = 600
PASS_RATE_THRESHOLD = 0.90
MIN_PASS_RATE_EARLY = 0.50  # obs app alert: loop > 5 with pass rate below this
```

---

## Observability App

Next.js 14 (App Router), Tailwind, polls PostgreSQL and subscribes to Redis pub/sub.

Key views: Dashboard · Session timeline · Event log · Agent state inspector · Metrics panel

Stuck-loop detection in the obs app is **display-only**. It surfaces alerts but takes no action. The orchestrator is the actor. Do not add loop control logic to the obs app.

---

## Security

- `ANTHROPIC_API_KEY` is in `.env` — never commit `.env` files
- PostgreSQL and Redis have no host port bindings
- Each workshop runs on its own named Docker network — cross-workshop communication is impossible by design
- `.env` files are gitignored; use `.env.example` as the template

---

## What Is Deliberately Excluded

- **Vector store (Qdrant etc.)** — not in the core stack. Add as an optional sidecar only if SME agents need semantic search over a large knowledge base.
- **LLM-evaluated exit criteria** — "orchestrator LLM confirms task completion" was removed as an exit signal because it is non-deterministic and makes benchmarking noisy. Exit is based on `pass_rate`, critique trend, and `MAX_ITERATIONS` only.

## Implementation Phases

### Phase 1 — Scaffold (complete)
Project structure, Dockerfiles, docker-compose.yml, shared/constants.py, db/migrations/001_init.sql, stub implementations for all services, obs-app Next.js skeleton, scripts/new_workshop.sh.

### Phase 2 — Core infrastructure layer (complete)
Shared DB and Redis connection modules (`shared/db.py`, `shared/redis_client.py`). No Claude API calls yet. Validate postgres/redis come up healthy and migrations apply. `retry.py` and `health.py` hardened.

### Phase 3 — Orchestrator brain (complete)
- `loop.py` — full sequential loop: assign coder → wait → assign reviewer → wait → assign QA → wait → evaluate exit criteria → repeat
- Redis channel conventions finalized (channel names, message envelope schema)
- Session lifecycle in postgres (create, update status, write iterations/events)
- Stuck detection (SHA-256 hash over STUCK_HASH_WINDOW iterations)
- Hang detection (HANG_TIMEOUT_S per iteration, dead-letter push, user escalation)

### Phase 4 — Core agents (complete)
- Coder, Reviewer, QA: actual Claude API calls via `call_api_with_retry()`
- Coder: `guard_payload()` live — diffs >8 MB written to artifact volume, path+summary sent via Redis
- Reviewer: enforces `{"critical":[], "major":[], "minor":[], "summary":""}` output schema
- QA: enforces `{"pass_rate":float, "passed":[], "failed":[], "coverage_delta":float, "notes":""}` output schema

### Phase 5 — SME agents (complete)
- `base_sme.py` cache logic live: Redis L1 (fast) → Postgres L2 (persistent), 24h TTL
- Cache key: `sha256(prompt_version + session_id + domain + question)`
- Each domain's system prompt tuned for its expertise area

### Phase 6 — Observability app (complete)
- Real session/iteration/event data polling from Postgres
- Redis pub/sub subscription for live event stream
- Stuck-loop alerts (display only — no loop control in obs-app)

---

## Learning Lessons

- Append to this CLAUDE.md file any hard won lessons and groom this file to be better at implementation. Anytime coding results in having to revert and attempt a new solution, store the reasons why the code failed and review those to become better at coding.

- **retry.py belongs in shared/, not services/orchestrator/** — Each agent service runs in its own Docker container. Placing retry.py in services/orchestrator/ means coder/reviewer/QA containers cannot import it (they don't have the orchestrator files). Any utility needed by multiple services must live in shared/ where all Dockerfiles copy it. services/orchestrator/retry.py now re-exports from shared/retry.py.

- **Claude JSON output needs robust extraction** — Claude sometimes wraps JSON in markdown fences (```json ... ```). Always use `extract_json()` from shared/retry.py before json.loads(). Plain `json.loads(raw_text)` will fail intermittently and produce silent fallbacks that mask real errors.

- **SME prompt_version must be derived from prompt content, not a static string** — `SME_PROMPT_VERSION` as a hardcoded constant in shared/constants.py requires a manual bump every time a system prompt changes, which is easy to forget. Instead, derive it in `BaseSME.__init__()` as `sha256(system_prompt)[:16]`. This way editing a domain's system_prompt automatically invalidates its cache entries with zero manual intervention.

- **Next.js rewrites cannot proxy SSE — use a Route Handler** — `next.config.js` rewrites buffer the full response before forwarding. SSE is an infinite stream, so a rewrite-proxied SSE endpoint will block forever and never deliver events. Always proxy SSE through an App Router Route Handler (`route.ts`) that returns `new Response(upstream.body, ...)` to pipe the stream with zero buffering. Two env vars are needed: `NEXT_PUBLIC_ORCHESTRATOR_URL` (browser-side, points to exposed host port) and `ORCHESTRATOR_INTERNAL_URL` (server-side, points to internal Docker service name).

- **asyncpg returns jsonb columns as dicts, not strings** — Unlike psycopg2/SQLAlchemy, asyncpg deserialises `jsonb` columns into Python dicts automatically. Do not call `json.loads()` on the result of a jsonb fetch — check `isinstance(raw, dict)` first or it will raise a TypeError.

- **Coder must receive previous reviewer/QA feedback** — On iterations > 1, the orchestrator must pass `reviewer_feedback` and `qa_feedback` from the previous iteration to the coder payload. Without this, the coder has no signal to improve and the loop will get stuck (identical outputs → STUCK_HASH_WINDOW trigger). loop.py tracks `_previous_outputs` and injects feedback into the coder payload each iteration.


