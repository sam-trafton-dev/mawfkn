"""
services/coder/main.py

The Coder agent.
Receives a task spec (and optionally previous reviewer/QA feedback) from the
orchestrator, calls Claude to implement the feature, writes code to the artifact
volume, and returns a structured payload.

Output schema:
    {
        "files":   {"<path>": "<content>", ...},
        "summary": "<one-paragraph description of what was implemented>",
        "notes":   "<implementation notes, decisions, caveats>"
    }
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.coder.tools import guard_payload
from shared.constants import MAX_TOKENS_CODER, MODEL
from shared.db import close_pool, emit_agent_event, get_agent_prompt, get_pool, seed_agent_prompt
from shared.redis_client import close_client, get_client
from shared.retry import call_api_with_retry, extract_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert software engineer (Coder agent) in a multi-agent workshop system.

## When task_spec contains input_files (improving existing code):

1. Read ALL files in input_files carefully before writing anything.
2. Understand the existing architecture, patterns, and conventions.
3. Follow the improvement_mode:
   - refactor: Improve code quality, readability, structure, type safety, error handling.
     Preserve ALL existing APIs, function signatures, and behavior exactly.
   - bugfix: Identify and fix specific bugs. Minimal changes — do not refactor unrelated code.
     Every changed line must directly address a bug.
   - feature: Add the requested new functionality. Integrate cleanly with existing patterns.
     Do not break existing behavior.
4. Output complete, improved versions of changed files in the "files" dict.
   Use the same relative paths as in input_files.
5. Do NOT truncate file content. Every output file must be complete and runnable.

## When task_spec has no input_files (greenfield):

1. Read the task specification carefully.
2. If reviewer_feedback or qa_feedback is provided, address every critical and major issue first.
3. Implement clean, production-quality, well-structured code.
4. Write tests alongside the implementation.

## Output format (always):

Return ONLY valid JSON — no markdown, no explanation outside the JSON:
{
  "files":   {"<relative/path/to/file.ext>": "<full file content>"},
  "summary": "<one paragraph: what was implemented/changed and why>",
  "notes":   "<decisions made, tradeoffs, what the reviewer should focus on>"
}

Rules:
- Every file in "files" must be complete and self-contained (not a diff, not partial).
- Do not truncate file content.
- If you cannot implement something, explain why in "notes" and leave "files" partial.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    get_client()
    pool = await get_pool()
    await seed_agent_prompt(pool, "coder", SYSTEM_PROMPT)
    logger.info("Coder agent started")
    yield
    await close_client()
    await close_pool()


app = FastAPI(title="MAWF Coder", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class CoderRunRequest(BaseModel):
    session_id: str
    loop_n: int
    task_spec: dict[str, Any] = {}
    reviewer_feedback: dict[str, Any] | None = None
    qa_feedback: dict[str, Any] | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run")
async def run(body: CoderRunRequest) -> dict[str, Any]:
    """Execute a coding iteration and return produced artifacts."""
    logger.info("Coder run — session=%s loop=%d", body.session_id, body.loop_n)

    user_content: dict[str, Any] = {
        "task_spec": body.task_spec,
        "loop_n": body.loop_n,
    }
    if body.reviewer_feedback:
        user_content["reviewer_feedback"] = body.reviewer_feedback
        user_content["instruction"] = (
            "Address all critical and major issues from reviewer_feedback before proceeding."
        )
    if body.qa_feedback:
        user_content["qa_feedback"] = body.qa_feedback

    pool = await get_pool()
    prompt = await get_agent_prompt(pool, "coder", SYSTEM_PROMPT)

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = await call_api_with_retry(
        client.messages.create,
        model=MODEL,
        max_tokens=MAX_TOKENS_CODER,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(user_content, indent=2)}],
    )

    raw_text = response.content[0].text if response.content else "{}"

    try:
        result = json.loads(extract_json(raw_text))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Coder JSON parse failed: %s — wrapping raw output", exc)
        result = {
            "files": {},
            "summary": raw_text[:500],
            "notes": f"JSON parse error: {exc}",
        }
        redis = get_client()
        await emit_agent_event(pool, redis, body.session_id, "coder", "json_parse_error", {
            "loop_n": body.loop_n,
            "error": str(exc),
            "hint": "Claude likely hit the output token limit mid-JSON. Consider a simpler task or breaking it into smaller steps.",
            "raw_preview": raw_text[:300],
        })

    result.setdefault("files", {})
    result.setdefault("summary", "")
    result.setdefault("notes", "")
    result["session_id"] = body.session_id
    result["loop_n"] = body.loop_n
    result["agent"] = "coder"

    # Guard large payloads — if >8 MB, offload to artifact volume
    redis = get_client()
    result = await guard_payload(
        payload=result,
        session_id=body.session_id,
        loop_n=body.loop_n,
        redis_client=redis,
    )

    return result
