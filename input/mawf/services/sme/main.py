"""
services/sme/main.py

FastAPI entrypoint shared by all six SME agents.
The SME_DOMAIN environment variable selects which domain to load.

Design rules:
  - Fresh SME instance per request (stateless — no shared state).
  - Shared asyncpg pool and Redis client across requests (infrastructure only).
  - All Claude calls go through call_api_with_retry() inside BaseSME.answer().
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from services.sme.base_sme import BaseSME
from shared.db import close_pool, get_pool, seed_agent_prompt
from shared.redis_client import close_client, get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Domain selection ───────────────────────────────────────────────────────────
SME_DOMAIN = os.environ.get("SME_DOMAIN", "data").lower()

_DOMAIN_MODULE_MAP: dict[str, str] = {
    "data":       "services.sme.domains.data",
    "api":        "services.sme.domains.api",
    "ux":         "services.sme.domains.ux",
    "business":   "services.sme.domains.business",
    "networking": "services.sme.domains.networking",
    "devops":     "services.sme.domains.devops",
}

_sme_class: type[BaseSME] | None = None


def _load_domain(domain: str) -> type[BaseSME]:
    """Import and return the SME class for the given domain name."""
    global _sme_class
    if _sme_class is not None:
        return _sme_class

    if domain not in _DOMAIN_MODULE_MAP:
        raise ValueError(
            f"Unknown SME_DOMAIN {domain!r}. Valid values: {list(_DOMAIN_MODULE_MAP)}"
        )

    import importlib
    mod = importlib.import_module(_DOMAIN_MODULE_MAP[domain])
    _sme_class = getattr(mod, "SME")
    return _sme_class


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Validate domain and warm the import at startup (fail fast)
    _load_domain(SME_DOMAIN)

    # Initialise shared infrastructure
    pool = await get_pool(min_size=1, max_size=4)
    get_client()

    # Seed default prompt so it's visible in the obs-app prompt editor
    sme = _load_domain(SME_DOMAIN)()
    await seed_agent_prompt(pool, f"sme-{SME_DOMAIN}", sme.system_prompt)

    logger.info("SME agent started — domain=%s", SME_DOMAIN)
    yield

    await close_client()
    await close_pool()
    logger.info("SME agent shutdown — domain=%s", SME_DOMAIN)


app = FastAPI(title=f"MAWF SME ({SME_DOMAIN})", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class SMERequest(BaseModel):
    session_id: str
    question: str


class SMEResponse(BaseModel):
    domain: str
    answer: str
    cached: bool


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=SMEResponse)
async def ask(body: SMERequest) -> SMEResponse:
    """
    Ask the domain SME a question.
    Returns a cached answer if one exists; calls Claude otherwise.
    A fresh SME instance is created per request (stateless by design).
    """
    sme_class = _load_domain(SME_DOMAIN)
    sme = sme_class()  # fresh instance — no shared state

    pool = await get_pool()
    redis = get_client()

    try:
        result = await sme.answer(
            question=body.question,
            session_id=body.session_id,
            redis_client=redis,
            pool=pool,
        )
    except Exception as exc:
        logger.exception("SME answer failed domain=%s: %s", SME_DOMAIN, exc)
        raise HTTPException(status_code=500, detail=f"SME error: {exc}") from exc

    return SMEResponse(
        domain=result["domain"],
        answer=result["answer"],
        cached=result.get("cached", False),
    )


@app.get("/domain")
async def domain_info() -> dict[str, Any]:
    """Return metadata about this SME's domain (useful for debugging)."""
    sme_class = _load_domain(SME_DOMAIN)
    sme = sme_class()
    return {
        "domain": sme.domain,
        "prompt_version": sme._prompt_version,
        "description": sme_class.__doc__ or "",
    }
