"""
services/orchestrator/main.py

FastAPI application entry point for the orchestrator service.
Exposes:
  GET  /health                        — liveness probe
  POST /sessions                      — create session and start loop
  GET  /sessions/{id}                 — fetch session status + iterations
  GET  /sessions/{id}/events          — SSE stream: history then live Redis feed
  POST /sessions/{id}/terminate       — force-stop the active loop
  GET  /sessions                      — list all sessions (for obs-app dashboard)

Lock ordering (to prevent deadlock):
  1. _loops_lock (asyncio.Lock) - guards _active_loops dict
  2. Database transactions - acquired after _loops_lock
  Never acquire _loops_lock inside a database transaction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import asyncpg
from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.orchestrator.health import get_monitor, reset_monitor
from services.orchestrator.loop import LoopController
from shared.constants import MAX_TOKENS_CHAT, MODEL
from shared.db import close_pool, get_pool, list_agent_prompts, log_event, set_agent_prompt, update_session_status
from shared.redis_client import close_client, events_channel, get_client, publish
from shared.retry import call_api_with_retry, extract_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Active loop registry ─────────────────────────────────────────────────────
# Protected by _loops_lock. Lock must be acquired BEFORE any DB transactions.
_active_loops: dict[str, LoopController] = {}
_loops_lock: asyncio.Lock | None = None


def _ensure_loops_lock() -> asyncio.Lock:
    """Lazily create the loops lock in the current event loop."""
    global _loops_lock
    if _loops_lock is None:
        _loops_lock = asyncio.Lock()
    return _loops_lock


ARTIFACT_PATH = Path(os.getenv("ARTIFACT_PATH", "/artifacts"))
INPUT_PATH = Path(os.getenv("INPUT_PATH", "/input"))
_INPUT_TOTAL_MAX = 150 * 1024   # 150 KB total injected
_INPUT_FILE_MAX  = 20  * 1024   # 20 KB per file

# ── Agent URLs (used by health monitor) ───────────────────────────────────────

AGENT_URLS: dict[str, str] = {
    "coder":          os.getenv("CODER_URL",         "http://coder:8001"),
    "reviewer":       os.getenv("REVIEWER_URL",      "http://reviewer:8002"),
    "qa":             os.getenv("QA_URL",            "http://qa:8003"),
    "sme-data":       os.getenv("SME_DATA_URL",      "http://sme-data:8080"),
    "sme-api":        os.getenv("SME_API_URL",       "http://sme-api:8080"),
    "sme-ux":         os.getenv("SME_UX_URL",        "http://sme-ux:8080"),
    "sme-business":   os.getenv("SME_BUSINESS_URL",  "http://sme-business:8080"),
    "sme-networking": os.getenv("SME_NETWORKING_URL","http://sme-networking:8080"),
    "sme-devops":     os.getenv("SME_DEVOPS_URL",    "http://sme-devops:8080"),
}


# ── Helper functions ─────────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    """Convert a string to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = re.sub(r"^-+|-+$", "", slug)
    return slug[:64]


def _safe_serialize(obj: Any, depth: int = 0, max_depth: int = 20, seen: set[int] | None = None) -> Any:
    """Safely serialize an object, handling circular references and special types.
    
    Args:
        obj: Object to serialize
        depth: Current recursion depth
        max_depth: Maximum recursion depth before truncating
        seen: Set of object ids already visited (for circular reference detection)
    """
    if seen is None:
        seen = set()
    
    if depth > max_depth:
        return "<max depth exceeded>"
    
    obj_id = id(obj)
    if obj_id in seen:
        return "<circular reference>"
    
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    
    if isinstance(obj, datetime):
        return obj.isoformat()
    
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    
    if isinstance(obj, dict):
        new_seen = seen | {obj_id}
        return {str(k): _safe_serialize(v, depth + 1, max_depth, new_seen) for k, v in obj.items()}
    
    if isinstance(obj, (list, tuple)):
        new_seen = seen | {obj_id}
        return [_safe_serialize(item, depth + 1, max_depth, new_seen) for item in obj]
    
    # For other objects, try to convert to string
    try:
        return str(obj)
    except Exception:
        return f"<unserializable: {type(obj).__name__}>"


def _row_to_dict(row: asyncpg.Record | None) -> dict[str, Any]:
    """Convert an asyncpg Record to a JSON-safe dictionary."""
    if row is None:
        return {}
    return _safe_serialize(dict(row))


def _sanitize_input_path(input_path: str) -> str:
    """Validate and sanitize input path to prevent directory traversal.
    
    Args:
        input_path: User-provided path relative to INPUT_PATH
        
    Returns:
        Sanitized path string
        
    Raises:
        ValueError: If path is invalid or attempts traversal
    """
    if not input_path:
        raise ValueError("Input path cannot be empty")
    
    # Reject absolute paths
    if input_path.startswith("/") or input_path.startswith("\\"):
        raise ValueError("Input path cannot be absolute")
    
    # Resolve and check for traversal
    normalized = Path(input_path).as_posix()
    if ".." in normalized.split("/"):
        raise ValueError("Input path cannot contain '..'")
    
    return normalized


def _validate_improvement_mode(mode: str | None) -> str:
    """Validate improvement mode, returning a default if not provided."""
    valid_modes = {"refactor", "bugfix", "feature"}
    if not mode:
        return "refactor"
    if mode not in valid_modes:
        logger.warning("Invalid improvement mode '%s', defaulting to 'refactor'", mode)
        return "refactor"
    return mode


def _load_input_files_sync(input_path: str) -> dict[str, Any]:
    """Walk /input/{input_path}/ and return included file contents + full tree.
    
    Returns:
        {
            "included": {"path": "content", ...},
            "tree": ["path1", "path2", ...],
            "truncated": bool,
            "error": str | None
        }
    """
    try:
        sanitized = _sanitize_input_path(input_path)
    except ValueError as e:
        return {"included": {}, "tree": [], "truncated": False, "error": str(e)}
    
    base = INPUT_PATH / sanitized
    if not base.exists() or not base.is_dir():
        return {"included": {}, "tree": [], "truncated": False, 
                "error": f"Path /input/{sanitized} not found"}

    SKIP_DIRS = {".git", "__pycache__", "node_modules", ".next", "dist", "build",
                 ".mypy_cache", ".ruff_cache", "venv", ".venv", "htmlcov", ".pytest_cache"}
    TEXT_EXTS  = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
                  ".c", ".cpp", ".h", ".json", ".yaml", ".yml", ".toml", ".md",
                  ".txt", ".sh", ".sql", ".css", ".env.example", ".gitignore"}
    NAMED_TEXT = {"Dockerfile", "Makefile", ".env.example", ".gitignore", ".dockerignore"}
    PRIORITY   = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}

    all_files: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(base).parts):
            continue
        if p.suffix.lower() in TEXT_EXTS or p.name in NAMED_TEXT:
            all_files.append(p)

    all_files.sort(key=lambda p: (0 if p.suffix.lower() in PRIORITY else 1, str(p.relative_to(base))))
    tree = [str(p.relative_to(base)) for p in all_files]

    included: dict[str, str] = {}
    total = 0
    truncated = False
    for p in all_files:
        if total >= _INPUT_TOTAL_MAX:
            truncated = True
            break
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            if len(content) > _INPUT_FILE_MAX:
                content = content[:_INPUT_FILE_MAX] + "\n... [file truncated at 20KB]"
            included[str(p.relative_to(base))] = content
            total += len(content)
        except Exception as e:
            logger.debug("Could not read file %s: %s", p, e)

    return {"included": included, "tree": tree, "truncated": truncated, "error": None}


_CHAT_SYSTEM = """\
You are the orchestrator for a multi-agent software development workshop.
Users talk to you in plain English to describe what they want built.

When a user asks you to build, create, implement, or start work on something,
extract a structured task spec from their message and respond with JSON in this format:

{
  "action": "create_session",
  "workshop_name": "<short slug, e.g. todo-api>",
  "artifact_name": "<optional, only if user explicitly named output folder>",
  "input_path": "<optional, relative path under /input/ to existing codebase, only if user mentions working on existing code>",
  "improvement_mode": "<optional, one of: refactor | bugfix | feature — only when input_path is set>",
  "task_spec": {
    "description": "<clear 1-3 sentence description of what to build>",
    "tech_stack": ["<language/framework>", ...],
    "requirements": ["<requirement 1>", "<requirement 2>", ...],
    "constraints": ["<any constraints or style preferences>"]
  },
  "reply": "<friendly conversational confirmation of what you understood and are about to build>"
}

Notes on optional fields:
- artifact_name: only include if the user explicitly named an output folder or project name. Use lowercase-with-hyphens format, e.g. my-todo-api. Omit entirely if the user did not specify a name.
- input_path: only include if the user is working on existing code (e.g. "refactor my project in ./input/mawf"). The value is the relative path under /input/, e.g. "mawf".
- improvement_mode: only include when input_path is set. One of: refactor (improve code quality/structure), bugfix (fix specific bugs with minimal changes), feature (add new functionality).

If the user is just asking a question or chatting (not requesting a build), respond with:

{
  "action": "reply",
  "reply": "<your response>"
}

Always respond with valid JSON only. No markdown fences, no preamble.\
"""


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Bring up shared infrastructure
    pool = await get_pool()
    redis = get_client()
    
    # Ensure artifact path exists
    ARTIFACT_PATH.mkdir(parents=True, exist_ok=True)

    # Wire health monitor with terminate callback
    monitor = await get_monitor()
    monitor.register_agents(AGENT_URLS)
    monitor.set_terminate_callback(_terminate_all_loops)
    await monitor.start()

    logger.info("Orchestrator started — asyncpg pool + Redis ready, health monitor active")
    yield

    # Teardown
    await monitor.stop()
    await reset_monitor()
    await close_client()
    await close_pool()
    logger.info("Orchestrator shutdown complete")


app = FastAPI(title="MAWF Orchestrator", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ──────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    workshop_name: str
    task_spec: dict[str, Any] = {}


class SessionResponse(BaseModel):
    session_id: str
    workshop_name: str
    status: str


class UpdatePromptRequest(BaseModel):
    content: str


class ContinueSessionRequest(BaseModel):
    instructions: str  # Natural language follow-up from the user


class ArtifactNameRequest(BaseModel):
    name: str


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str | None = None
    workshop_name: str | None = None


# ── Terminate callback for health monitor ────────────────────────────────────

async def _terminate_all_loops(reason: str) -> None:
    """Terminate all active loops. Called by health monitor when agent dies."""
    lock = _ensure_loops_lock()
    async with lock:
        controllers = list(_active_loops.values())
    
    # Terminate outside of lock to prevent deadlock
    for controller in controllers:
        try:
            await asyncio.wait_for(controller.terminate(reason=reason), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for controller to terminate: %s", controller.session_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to terminate controller %s: %s", controller.session_id, exc)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", response_model=SessionResponse, status_code=202)
async def create_session(body: CreateSessionRequest) -> SessionResponse:
    """Create a new session row and kick off the agent loop as a background task."""
    session_id = str(uuid.uuid4())
    pool = await get_pool()

    await pool.execute(
        """
        INSERT INTO sessions (id, workshop_name, task_spec, status)
        VALUES ($1, $2, $3::jsonb, 'pending')
        """,
        session_id,
        body.workshop_name,
        json.dumps(body.task_spec),
    )

    asyncio.create_task(
        _run_loop_background(session_id, body.task_spec),
        name=f"loop-{session_id}",
    )

    return SessionResponse(
        session_id=session_id,
        workshop_name=body.workshop_name,
        status="pending",
    )


@app.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    """Return all sessions ordered by creation time descending."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, workshop_name, status, created_at, updated_at FROM sessions ORDER BY created_at DESC"
    )
    return [_row_to_dict(r) for r in rows]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Fetch session status and its iteration history."""
    pool = await get_pool()

    session_row = await pool.fetchrow(
        "SELECT * FROM sessions WHERE id = $1", session_id
    )
    if session_row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    iterations = await pool.fetch(
        "SELECT loop_n, outputs, test_pass_rate, created_at FROM iterations "
        "WHERE session_id = $1 ORDER BY loop_n ASC",
        session_id,
    )

    return {
        **_row_to_dict(session_row),
        "iterations": [_row_to_dict(r) for r in iterations],
    }


@app.get("/sessions/{session_id}/events")
async def stream_events(session_id: str) -> StreamingResponse:
    """
    Server-Sent Events stream for the given session.
    Replays all historical events from Postgres, then tails the live
    Redis pub/sub channel for new events until the client disconnects.
    """
    pool = await get_pool()

    # Verify session exists
    exists = await pool.fetchval("SELECT 1 FROM sessions WHERE id = $1", session_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        # 1. Replay history from Postgres
        history = await pool.fetch(
            "SELECT agent_role, event_type, payload, ts FROM events "
            "WHERE session_id = $1 ORDER BY id ASC",
            session_id,
        )
        for row in history:
            data = json.dumps({
                "agent_role": row["agent_role"],
                "event_type": row["event_type"],
                "payload": row["payload"],
                "ts": row["ts"].isoformat(),
                "source": "history",
            })
            yield f"data: {data}\n\n"

        # 2. Tail live Redis pub/sub
        redis = get_client()
        pubsub = redis.pubsub()
        channel = events_channel(session_id)
        channel_subscribed = False
        
        try:
            await pubsub.subscribe(channel)
            channel_subscribed = True
            
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    yield f"data: {msg['data']}\n\n"
                else:
                    # Keep-alive comment to prevent proxy timeouts
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Clean up pubsub resources
            try:
                if channel_subscribed:
                    await pubsub.unsubscribe(channel)
            except Exception as unsub_err:
                logger.warning("Error unsubscribing from channel: %s", unsub_err)
            
            try:
                await pubsub.aclose()
            except Exception as close_err:
                logger.warning("Error closing pubsub: %s", close_err)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/prompts")
async def list_prompts() -> list[dict]:
    """Return all agent prompts stored in the DB."""
    pool = await get_pool()
    return await list_agent_prompts(pool)


@app.get("/prompts/{agent_role}")
async def get_prompt(agent_role: str) -> dict:
    """Return the current system prompt for one agent."""
    pool = await get_pool()
    rows = await list_agent_prompts(pool)
    for row in rows:
        if row["agent_role"] == agent_role:
            return row
    raise HTTPException(status_code=404, detail=f"No prompt found for agent '{agent_role}'")


@app.put("/prompts/{agent_role}", status_code=200)
async def update_prompt(agent_role: str, body: UpdatePromptRequest) -> dict:
    """Overwrite the system prompt for an agent. Takes effect on the next request to that agent."""
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Prompt content cannot be empty")
    pool = await get_pool()
    await set_agent_prompt(pool, agent_role, body.content)
    logger.info("Prompt updated for agent '%s'", agent_role)
    return {"agent_role": agent_role, "status": "updated"}


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    """
    Natural language interface to the orchestrator.
    Parse the user's message with Claude and, if a build is requested,
    automatically create a session and start the loop.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    
    client = AsyncAnthropic(api_key=api_key)
    response = await call_api_with_retry(
        client.messages.create,
        model=MODEL,
        max_tokens=MAX_TOKENS_CHAT,
        system=_CHAT_SYSTEM,
        messages=[{"role": "user", "content": body.message}],
    )
    raw = response.content[0].text if response.content else "{}"

    try:
        parsed = json.loads(extract_json(raw))
    except (ValueError, json.JSONDecodeError):
        return ChatResponse(reply="Sorry, I couldn't parse that. Could you rephrase what you'd like to build?")

    action = parsed.get("action", "reply")

    if action == "create_session":
        workshop_name = parsed.get("workshop_name", "workshop")
        task_spec = parsed.get("task_spec", {})
        reply_text = parsed.get("reply", f"Starting work on {workshop_name}…")

        # If Claude extracted an explicit artifact name, store it in task_spec
        raw_artifact_name = parsed.get("artifact_name", "").strip()
        if raw_artifact_name:
            task_spec["artifact_name"] = _slugify(raw_artifact_name)

        # If Claude extracted an input_path, load the files and inject into task_spec
        input_path = parsed.get("input_path", "").strip()
        improvement_mode = parsed.get("improvement_mode", "")
        if input_path:
            file_data = await asyncio.to_thread(_load_input_files_sync, input_path)
            if file_data.get("error"):
                return ChatResponse(
                    reply=f"Could not load input files: {file_data['error']}. "
                          "Please check the path and try again."
                )
            task_spec["input_path"] = input_path
            task_spec["input_files"] = file_data["included"]
            task_spec["input_file_tree"] = file_data["tree"]
            task_spec["input_truncated"] = file_data["truncated"]
            task_spec["improvement_mode"] = _validate_improvement_mode(improvement_mode)

        session_id = str(uuid.uuid4())
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO sessions (id, workshop_name, task_spec, status) VALUES ($1, $2, $3::jsonb, 'pending')",
            session_id,
            workshop_name,
            json.dumps(task_spec),
        )
        asyncio.create_task(
            _run_loop_background(session_id, task_spec),
            name=f"loop-{session_id}",
        )
        logger.info("Chat created session %s — %s", session_id, workshop_name)
        return ChatResponse(reply=reply_text, session_id=session_id, workshop_name=workshop_name)

    return ChatResponse(reply=parsed.get("reply", raw))


@app.post("/sessions/{session_id}/continue", response_model=SessionResponse, status_code=202)
async def continue_session(session_id: str, body: ContinueSessionRequest) -> SessionResponse:
    """
    Resume a stopped/failed/completed session with additional instructions.
    Loads all prior iteration context so the agents can build on existing work.
    
    Lock ordering: _loops_lock is checked BEFORE database transaction to prevent deadlock.
    """
    lock = _ensure_loops_lock()
    
    # Check if loop is already active BEFORE starting DB transaction
    async with lock:
        if session_id in _active_loops:
            raise HTTPException(status_code=409, detail="Session loop is already running")
    
    # Now safe to do database operations
    pool = await get_pool()
    session_row = await pool.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Load previous outputs for context
    iterations = await pool.fetch(
        "SELECT loop_n, outputs FROM iterations WHERE session_id = $1 ORDER BY loop_n ASC",
        session_id,
    )
    previous_outputs = [_row_to_dict(r) for r in iterations]

    # Merge new instructions into the existing task_spec
    task_spec = session_row["task_spec"] or {}
    if isinstance(task_spec, str):
        task_spec = json.loads(task_spec)
    task_spec = dict(task_spec)
    task_spec["follow_up_instructions"] = body.instructions
    task_spec["previous_iteration_count"] = len(previous_outputs)

    # Reset status to pending and update task_spec with new instructions
    await pool.execute(
        "UPDATE sessions SET status = 'pending', task_spec = $1::jsonb WHERE id = $2",
        json.dumps(task_spec),
        session_id,
    )

    asyncio.create_task(
        _run_loop_background(session_id, task_spec, resume_from=previous_outputs),
        name=f"loop-{session_id}",
    )
    logger.info("Session %s continued with instructions: %s", session_id, body.instructions[:100])

    return SessionResponse(
        session_id=session_id,
        workshop_name=session_row["workshop_name"],
        status="pending",
    )


@app.post("/sessions/{session_id}/terminate", status_code=202)
async def terminate_session(session_id: str) -> dict[str, str]:
    """Signal the active loop for this session to stop after the current iteration."""
    lock = _ensure_loops_lock()
    async with lock:
        controller = _active_loops.get(session_id)
    
    if controller is None:
        raise HTTPException(status_code=404, detail="No active loop for this session")
    
    await controller.terminate(reason="API request")
    return {"status": "terminating"}


@app.post("/sessions/{session_id}/artifact-name", status_code=200)
async def set_artifact_name(session_id: str, body: ArtifactNameRequest) -> dict[str, str]:
    """
    Set a human-readable name for this session's artifact output folder.
    Renames the folder on disk (if it exists) and persists the name in task_spec.
    """
    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(status_code=422, detail="Invalid name — must contain at least one alphanumeric character")

    pool = await get_pool()
    row = await pool.fetchrow("SELECT task_spec FROM sessions WHERE id = $1", session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    task_spec = dict(row["task_spec"] or {})
    old_folder = task_spec.get("artifact_name") or session_id

    # Rename on disk if the old folder exists and the name is actually changing
    old_path = ARTIFACT_PATH / old_folder
    new_path = ARTIFACT_PATH / slug
    if old_path.exists() and old_path != new_path:
        if new_path.exists():
            raise HTTPException(status_code=409, detail=f"Folder '{slug}' already exists in artifacts")
        await asyncio.to_thread(old_path.rename, new_path)
        logger.info("Renamed artifact folder %s → %s", old_path, new_path)

    task_spec["artifact_name"] = slug
    await pool.execute(
        "UPDATE sessions SET task_spec = $1::jsonb WHERE id = $2",
        json.dumps(task_spec),
        session_id,
    )
    logger.info("Artifact name set: session=%s name=%s", session_id, slug)
    return {"artifact_name": slug}


# ── Background loop runner ───────────────────────────────────────────────────

async def _run_loop_background(
    session_id: str,
    task_spec: dict[str, Any],
    resume_from: list[dict[str, Any]] | None = None,
) -> None:
    """Background task that runs the agent loop for a session."""
    pool = await get_pool()
    redis = get_client()
    lock = _ensure_loops_lock()

    controller = LoopController(
        session_id=session_id,
        task_spec=task_spec,
        pool=pool,
        redis=redis,
        resume_from=resume_from,
    )

    # Register controller under lock
    async with lock:
        _active_loops[session_id] = controller

    try:
        await controller.run()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Loop crashed for session %s: %s", session_id, exc)
        try:
            await update_session_status(pool, session_id, "failed")
        except Exception as db_err:
            logger.error("Failed to update session status: %s", db_err)
    finally:
        # Unregister controller under lock
        async with lock:
            _active_loops.pop(session_id, None)
