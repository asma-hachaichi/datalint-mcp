"""Run a scenario: a real model audits planted data through the real MCP server.

The loop is written out rather than using the SDK's tool runner because every tool call has
to go through an MCP client connected to the server over stdio (exactly as Claude Code would
run it), and each call is recorded for scoring.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from datalint_mcp.server import INSTRUCTIONS

from .scenarios import Scenario
from .scoring import Run, ToolCall

ROOT = Path(__file__).resolve().parents[1]
MAX_TURNS = 20


def system_prompt() -> str:
    """The skill users install, plus the server's own instructions, as Claude would see them."""
    skill = (ROOT / "skills" / "datalint" / "SKILL.md").read_text(encoding="utf-8")
    skill = re.sub(r"\A---.*?---\s*", "", skill, flags=re.DOTALL)  # drop the frontmatter
    return (
        f"{skill}\n\n## Server instructions\n\n{INSTRUCTIONS}\n\n"
        "The user's files are in the folder the datalint server may read. Refer to them by "
        "file name."
    )


def _result_text(result: Any) -> str:
    texts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    if texts:
        return "\n".join(texts)
    return json.dumps(result.structured_content or {})


async def run_scenario(llm: Any, scenario: Scenario, model: str, effort: str = "medium",
                       max_turns: int = MAX_TURNS) -> Run:
    """Run one scenario. `llm` is an anthropic.AsyncAnthropic client (or a test double)."""
    run = Run(scenario=scenario.id, model=model)
    with tempfile.TemporaryDirectory(prefix=f"datalint-eval-{scenario.id}-") as folder:
        scenario.build(Path(folder))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "datalint_mcp"],
            env={**os.environ, "DATALINT_ROOTS": folder},
        )
        try:
            async with Client(params) as mcp:
                listed = (await mcp.list_tools()).tools
                tools = [{"name": t.name, "description": t.description or "",
                          "input_schema": t.input_schema} for t in listed]
                await _converse(llm, mcp, run, scenario, tools, model, effort, max_turns)
        except Exception as exc:  # recorded and scored as a failed run, never hidden
            run.error = _describe(exc)
    return run


def _describe(exc: BaseException) -> str:
    """The real cause, not the "unhandled errors in a TaskGroup" wrapper around it."""
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


async def _converse(llm: Any, mcp: Client, run: Run, scenario: Scenario, tools: list[dict],
                    model: str, effort: str, max_turns: int) -> None:
    system = system_prompt()
    messages: list[dict] = [{"role": "user", "content": scenario.prompt}]
    for _ in range(max_turns):
        response = await llm.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},  # tools and system repeat on every turn
        )
        run.turns += 1
        usage = response.usage
        run.input_tokens += usage.input_tokens or 0
        run.output_tokens += usage.output_tokens or 0
        run.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        run.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        run.stop_reason = response.stop_reason or ""

        # Thinking blocks must go back unchanged, so append the whole content.
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            run.report = "\n".join(b.text for b in response.content if b.type == "text")
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            outcome = await mcp.call_tool(block.name, block.input)
            text = _result_text(outcome)
            run.tool_calls.append(ToolCall(block.name, dict(block.input), text,
                                           bool(outcome.is_error)))
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                            "is_error": bool(outcome.is_error)})
        # All results of one turn go back in a single message.
        messages.append({"role": "user", "content": results})
    run.error = f"No final report after {max_turns} turns."
