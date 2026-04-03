"""
services/reviewer/main.py

The Reviewer agent.
Performs adversarial critique of the Coder's output.

Output schema (do not change — orchestrator and obs-app parse this):
    {
        "critical": ["<issue>", ...],
        "major":    ["<issue>", ...],
        "minor":    ["<issue>", ...],
        "summary":  "<one sentence assessment>"
    }
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.constants import MAX_TOKENS_REVIEWER, MODEL
from shared.db import close_pool, emit_agent_event, get_agent_prompt, get_pool, seed_agent_prompt
from shared.redis_client import close_client, get_client
from shared.retry import call_api_with_retry, extract_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ARTIFACT_PATH = Path(os.getenv("ARTIFACT_PATH", "/artifacts"))

SYSTEM_PROMPT = """\
You are an adversarial code reviewer (Reviewer agent) in a multi-agent workshop system.
Your job is to find every real problem with the Coder's implementation.

Assume bugs exist. Do not be lenient.

## When reviewing improvements to existing code (input_files present in coder_output context):

Check the improvement_mode was respected:
- refactor: Verify APIs and behavior are IDENTICAL to the original. Flag any behavioral changes.
- bugfix: Verify only bug-related lines changed. Flag unrelated modifications.
- feature: Verify new code integrates cleanly and existing tests still pass.

## Severity definitions:

  critical — security vulnerabilities, data loss, crashes, complete feature failures,
             API breakage (for refactor/bugfix modes)
  major    — correctness bugs, missing required functionality, significant performance issues
  minor    — style, naming, missing docs, minor inefficiencies, non-blocking improvements

Return ONLY valid JSON — no markdown, no text outside the JSON:
{
  "critical": ["<specific, actionable issue description>", ...],
  "major":    ["<specific, actionable issue description>", ...],
  "minor":    ["<specific, actionable issue description>", ...],
  "summary":  "<one sentence: overall assessment>"
}

If a category has no issues, return an empty list.
Be specific: include file names, line references, and concrete fix suggestions.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    get_client()
    pool = await get_pool()
    await seed_agent_prompt(pool, "reviewer", SYSTEM_PROMPT)
    logger.info("Reviewer agent started")
    yield
    await close_client()
    await close_pool()


app = FastAPI(title="MAWF Reviewer", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ReviewerRunRequest(BaseModel):
    session_id: str
    loop_n: int
    coder_output: dict[str, Any] = {}


class ReviewerOutput(BaseModel):
    critical: list[str]
    major: list[str]
    minor: list[str]
    summary: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run", response_model=ReviewerOutput)
async def run(body: ReviewerRunRequest) -> ReviewerOutput:
    """Critique the coder output and return structured review."""
    logger.info("Reviewer run — session=%s loop=%d", body.session_id, body.loop_n)

    coder_output = body.coder_output

    # If coder offloaded to artifact volume, load the full content for review
    if coder_output.get("__artifact__"):
        artifact_file = Path(coder_output.get("artifact_path", ""))
        if artifact_file.exists():
            try:
                coder_output = json.loads(artifact_file.read_bytes())
                logger.info("Reviewer loaded artifact from %s", artifact_file)
            except Exception as exc:
                logger.warning("Failed to load artifact for review: %s", exc)
                # Fall back to reviewing the reference summary
        else:
            logger.warning("Artifact not found at %s — reviewing summary only", artifact_file)

    user_content = {
        "session_id": body.session_id,
        "loop_n": body.loop_n,
        "coder_output": coder_output,
    }

    pool = await get_pool()
    prompt = await get_agent_prompt(pool, "reviewer", SYSTEM_PROMPT)

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = await call_api_with_retry(
        client.messages.create,
        model=MODEL,
        max_tokens=MAX_TOKENS_REVIEWER,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(user_content, indent=2)}],
    )

    raw_text = response.content[0].text if response.content else "{}"

    try:
        parsed = json.loads(extract_json(raw_text))
        return ReviewerOutput(
            critical=_ensure_str_list(parsed.get("critical", [])),
            major=_ensure_str_list(parsed.get("major", [])),
            minor=_ensure_str_list(parsed.get("minor", [])),
            summary=str(parsed.get("summary", "No summary provided.")),
        )
    except (ValueError, json.JSONDecodeError, Exception) as exc:
        logger.warning("Reviewer output parse failed: %s — raw: %.300s", exc, raw_text)
        await emit_agent_event(pool, get_client(), body.session_id, "reviewer", "json_parse_error", {
            "loop_n": body.loop_n,
            "error": str(exc),
            "raw_preview": raw_text[:300],
        })
        return ReviewerOutput(
            critical=[],
            major=[f"Reviewer output could not be parsed: {exc}"],
            minor=[],
            summary="Review parse error — treating output as a major issue.",
        )


def _ensure_str_list(val: Any) -> list[str]:
    """Coerce a value to list[str], handling Claude returning strings or None."""
    if isinstance(val, list):
        return [str(item) for item in val]
    if isinstance(val, str):
        return [val] if val else []
    return []
