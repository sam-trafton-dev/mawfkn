"""
services/qa/main.py

The QA agent.
Evaluates the Coder's implementation against the Reviewer's critique.
Designs and "runs" a test bench, returning structured pass/fail results.

Output schema (do not change — orchestrator uses pass_rate for exit criteria):
    {
        "pass_rate":      <float 0.0–1.0>,
        "passed":         ["<test name>", ...],
        "failed":         ["<test name>: <reason>", ...],
        "coverage_delta": <float, positive = improvement>,
        "notes":          "<important observations>"
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess  # noqa: F401 — available for future use
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared.constants import MAX_TOKENS_QA, MODEL, PASS_RATE_THRESHOLD
from shared.db import close_pool, emit_agent_event, get_agent_prompt, get_pool, seed_agent_prompt
from shared.redis_client import close_client, get_client
from shared.retry import call_api_with_retry, extract_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ARTIFACT_PATH = Path(os.getenv("ARTIFACT_PATH", "/artifacts"))
INPUT_PATH = Path(os.getenv("INPUT_PATH", "/input"))

SYSTEM_PROMPT = f"""\
You are an expert QA engineer (QA agent) in a multi-agent workshop system.

## When REAL test results are provided (real_test_results field is present):

The results come from actually running the test suite. Use them as-is for pass_rate, passed, failed.
Your job is to:
1. Analyze WHY tests failed — look at the coder output and reviewer critique.
2. Identify root causes and suggest fixes in "notes".
3. Assess if the improvement_mode was respected.

## When no real tests are available:

1. Review the Coder's implementation and the Reviewer's critique.
2. Design a comprehensive test bench covering: unit tests, integration tests, edge cases,
   error handling, and any issues flagged by the reviewer.
3. Simulate running those tests against the implementation.
4. Report results honestly — the pass_rate threshold for success is {PASS_RATE_THRESHOLD:.0%}.

## When reviewing improvements to existing code:
- bugfix/refactor: all previously passing tests must still pass (regression = critical fail).
- feature: existing tests pass + new feature tests pass.

Return ONLY valid JSON — no markdown, no text outside the JSON:
{{
  "pass_rate":      <float between 0.0 and 1.0>,
  "passed":         ["<test name>", ...],
  "failed":         ["<test name>: <specific reason>", ...],
  "coverage_delta": <float — positive means coverage improved>,
  "notes":          "<key observations, what still needs to be fixed>"
}}

Rules:
- pass_rate = len(passed) / (len(passed) + len(failed))
- Be realistic. Do not inflate pass_rate.
- Every critical/major reviewer issue that is not fixed should appear in failed.
- coverage_delta = 0.0 on the first iteration (no baseline).
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    get_client()
    pool = await get_pool()
    await seed_agent_prompt(pool, "qa", SYSTEM_PROMPT)
    logger.info("QA agent started")
    yield
    await close_client()
    await close_pool()


app = FastAPI(title="MAWF QA", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class QARunRequest(BaseModel):
    session_id: str
    loop_n: int
    coder_output: dict[str, Any] = {}
    reviewer_output: dict[str, Any] = {}
    artifact_name: str | None = None
    input_path: str | None = None


class QAOutput(BaseModel):
    pass_rate: float = Field(ge=0.0, le=1.0)
    passed: list[str]
    failed: list[str]
    coverage_delta: float
    notes: str


def _detect_project_type(output_dir: Path) -> str | None:
    """Return 'pytest', 'node', or None."""
    if (output_dir / "requirements.txt").exists() or (output_dir / "pyproject.toml").exists():
        return "pytest"
    if list(output_dir.rglob("*.py")):
        return "pytest"
    if (output_dir / "package.json").exists():
        return "node"
    return None


async def _run_real_tests(output_dir: Path, timeout: int = 120) -> dict[str, Any] | None:
    """Run actual tests against output_dir. Returns result dict or None if not applicable."""
    if not output_dir.exists():
        return None

    project_type = _detect_project_type(output_dir)
    if not project_type:
        return None

    if project_type == "pytest":
        # Try to install requirements if present
        req_file = output_dir / "requirements.txt"
        if req_file.exists():
            try:
                install_proc = await asyncio.create_subprocess_exec(
                    "pip", "install", "-r", str(req_file), "-q", "--no-warn-script-location",
                    cwd=str(output_dir),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(install_proc.communicate(), timeout=30)
            except Exception:
                pass  # Best-effort deps install

        # Actually run pytest
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest", "--tb=short", "-q", "--no-header",
                cwd=str(output_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output_text = stdout.decode(errors="replace")
            return _parse_pytest_output(output_text, proc.returncode)
        except asyncio.TimeoutError:
            return {
                "pass_rate": 0.0, "passed": [], "failed": ["Tests timed out after 120s"],
                "coverage_delta": 0.0, "notes": "Real test execution timed out after 120s.",
                "real_tests": True,
            }
        except Exception:
            return None  # Fall back to LLM

    return None  # node support future


def _parse_pytest_output(output: str, returncode: int) -> dict[str, Any]:
    """Parse pytest -q output into QA result dict."""
    passed_names: list[str] = []
    failed_names: list[str] = []

    for line in output.splitlines():
        if " PASSED" in line:
            passed_names.append(line.split(" PASSED")[0].strip())
        elif " FAILED" in line:
            failed_names.append(line.split(" FAILED")[0].strip())

    # Summary line: "5 passed, 2 failed in 1.23s" or "5 passed in 1.23s" or "no tests ran"
    summary_match = re.search(
        r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?",
        output,
    )
    if summary_match:
        n_passed = int(summary_match.group(1))
        n_failed = int(summary_match.group(2) or 0)
    else:
        n_passed = len(passed_names)
        n_failed = len(failed_names)

    total = n_passed + n_failed
    pass_rate = n_passed / total if total > 0 else (1.0 if returncode == 0 else 0.0)
    pass_rate = max(0.0, min(1.0, pass_rate))

    # If no test names parsed individually, reconstruct from summary
    if not passed_names and n_passed > 0:
        passed_names = [f"{n_passed} test(s) passed"]
    if not failed_names and n_failed > 0:
        failed_names = [f"{n_failed} test(s) failed"]

    # Grab FAILED details for notes
    fail_details: list[str] = []
    in_fail = False
    for line in output.splitlines():
        if line.startswith("FAILED "):
            fail_details.append(line)
        elif "short test summary" in line.lower():
            in_fail = True
        elif in_fail and line.startswith("FAILED"):
            fail_details.append(line)

    notes = f"Real pytest results: {n_passed} passed, {n_failed} failed."
    if fail_details:
        notes += "\nFailed: " + "; ".join(fail_details[:5])

    return {
        "pass_rate": round(pass_rate, 4),
        "passed": passed_names[:50],
        "failed": failed_names[:50],
        "coverage_delta": 0.0,
        "notes": notes,
        "real_tests": True,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run", response_model=QAOutput)
async def run(body: QARunRequest) -> QAOutput:
    """Run test bench evaluation and return structured QA results."""
    logger.info("QA run — session=%s loop=%d", body.session_id, body.loop_n)

    pool = await get_pool()
    prompt = await get_agent_prompt(pool, "qa", SYSTEM_PROMPT)

    # ── Attempt real test execution ────────────────────────────────────────────
    output_dir = ARTIFACT_PATH / (body.artifact_name or body.session_id) / "src"
    real_results = await _run_real_tests(output_dir)

    if real_results:
        logger.info("QA real tests — session=%s pass_rate=%.2f", body.session_id, real_results["pass_rate"])
        await emit_agent_event(pool, get_client(), body.session_id, "qa", "real_tests_ran", {
            "loop_n": body.loop_n,
            "pass_rate": real_results["pass_rate"],
            "output_dir": str(output_dir),
        })

    # ── Build LLM context (always run LLM for analysis/notes) ─────────────────
    coder_output = body.coder_output
    if coder_output.get("__artifact__"):
        artifact_file = Path(coder_output.get("artifact_path", ""))
        if artifact_file.exists():
            try:
                coder_output = json.loads(artifact_file.read_bytes())
            except Exception as exc:
                logger.warning("Failed to load artifact for QA: %s", exc)

    user_content: dict[str, Any] = {
        "session_id": body.session_id,
        "loop_n": body.loop_n,
        "coder_output": coder_output,
        "reviewer_output": body.reviewer_output,
    }
    if real_results:
        user_content["real_test_results"] = real_results
    if body.input_path:
        user_content["improvement_mode"] = "see task_spec in coder_output"

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = await call_api_with_retry(
        client.messages.create,
        model=MODEL,
        max_tokens=MAX_TOKENS_QA,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(user_content, indent=2)}],
    )

    raw_text = response.content[0].text if response.content else "{}"

    try:
        parsed = json.loads(extract_json(raw_text))
        # If real tests ran, use their pass_rate (trust real over LLM)
        if real_results:
            parsed["pass_rate"] = real_results["pass_rate"]
            if not parsed.get("passed"):
                parsed["passed"] = real_results.get("passed", [])
            if not parsed.get("failed"):
                parsed["failed"] = real_results.get("failed", [])
        pass_rate = float(parsed.get("pass_rate", 0.0))
        pass_rate = max(0.0, min(1.0, pass_rate))
        return QAOutput(
            pass_rate=pass_rate,
            passed=_ensure_str_list(parsed.get("passed", [])),
            failed=_ensure_str_list(parsed.get("failed", [])),
            coverage_delta=float(parsed.get("coverage_delta", 0.0)),
            notes=str(parsed.get("notes", "")),
        )
    except (ValueError, json.JSONDecodeError, Exception) as exc:
        logger.warning("QA output parse failed: %s — raw: %.300s", exc, raw_text)
        # If real tests ran, use those results even if LLM parsing failed
        if real_results:
            return QAOutput(
                pass_rate=real_results["pass_rate"],
                passed=real_results.get("passed", []),
                failed=real_results.get("failed", []),
                coverage_delta=0.0,
                notes=real_results.get("notes", "LLM analysis failed; using real test results."),
            )
        await emit_agent_event(pool, get_client(), body.session_id, "qa", "json_parse_error", {
            "loop_n": body.loop_n,
            "error": str(exc),
            "raw_preview": raw_text[:300],
        })
        return QAOutput(
            pass_rate=0.0,
            passed=[],
            failed=[f"QA output parse error: {exc}"],
            coverage_delta=0.0,
            notes=f"Parse error — raw output: {raw_text[:300]}",
        )


def _ensure_str_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(item) for item in val]
    if isinstance(val, str):
        return [val] if val else []
    return []
