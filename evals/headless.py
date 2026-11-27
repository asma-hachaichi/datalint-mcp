"""Run the eval scenarios through headless Claude Code sessions instead of the API.

Each run is its own `claude -p` process. It can use only the datalint MCP server (built-in
tools are switched off), gets the datalint skill appended to its system prompt, audits one
scenario's planted data, and its final report is scored with the same checks as `python -m
evals`. It uses the Claude Code login on this machine, so no API key is needed.

    python -m evals.headless --server path/to/datalint-mcp --repeat 2 --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .runner import system_prompt
from .scenarios import BY_ID, SCENARIOS, Scenario
from .scoring import Result, Run, ToolCall, score

TOOL_PREFIX = "mcp__datalint__"
# Set by the Claude Code session this may be launched from; a child session must not see them.
PARENT_SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


def parse_stream(lines: list[str], run: Run) -> dict:
    """Fill `run` from a stream-json transcript. Returns extra facts the score doesn't cover."""
    calls: dict[str, ToolCall] = {}
    extra = {"other_tools": [], "server_status": None, "cost_usd": None}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            servers = {s.get("name"): s.get("status") for s in event.get("mcp_servers", [])}
            extra["server_status"] = servers.get("datalint")
        elif kind == "assistant":
            run.turns += 1
            for block in event.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name.startswith(TOOL_PREFIX):
                    call = ToolCall(name[len(TOOL_PREFIX):], block.get("input") or {}, "", False)
                    calls[block.get("id")] = call
                    run.tool_calls.append(call)
                else:
                    extra["other_tools"].append(name)
        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                call = calls.get(block.get("tool_use_id"))
                if block.get("type") != "tool_result" or call is None:
                    continue
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                call.result = str(body or "")
                call.is_error = bool(block.get("is_error"))
        elif kind == "result":
            run.report = event.get("result") or ""
            ok = event.get("subtype") == "success" and not event.get("is_error")
            run.stop_reason = "end_turn" if ok else str(event.get("subtype"))
            usage = event.get("usage") or {}
            run.input_tokens = usage.get("input_tokens", 0) or 0
            run.output_tokens = usage.get("output_tokens", 0) or 0
            run.cache_read_tokens = usage.get("cache_read_input_tokens", 0) or 0
            run.cache_write_tokens = usage.get("cache_creation_input_tokens", 0) or 0
            extra["cost_usd"] = event.get("total_cost_usd")
    if extra["server_status"] not in (None, "connected"):
        run.error = f"datalint server did not connect (status: {extra['server_status']})"
    elif not run.report and run.error is None:
        run.error = "The session ended without a final result."
    return extra


async def run_one(scenario: Scenario, server: str, model: str, timeout: float) -> tuple[Run, dict]:
    run = Run(scenario=scenario.id, model=model)
    with tempfile.TemporaryDirectory(prefix=f"datalint-headless-{scenario.id}-") as work:
        data = Path(work) / "data"
        data.mkdir()
        scenario.build(data)
        config = Path(work) / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"datalint": {
            "command": server, "args": [], "env": {"DATALINT_ROOTS": str(data)},
        }}}), encoding="utf-8")
        command = [
            shutil.which("claude") or "claude", "-p", scenario.prompt,
            "--model", model,
            "--tools", "",                          # no built-in tools: datalint only
            "--strict-mcp-config", "--mcp-config", str(config),
            "--allowedTools", "mcp__datalint",
            "--append-system-prompt", system_prompt(),
            "--setting-sources", "project,local",   # keep the user's own plugins and hooks out
            "--no-session-persistence",
            "--output-format", "stream-json", "--verbose",
        ]
        env = {k: v for k, v in os.environ.items() if k not in PARENT_SESSION_VARS}
        process = await asyncio.create_subprocess_exec(
            *command, cwd=data, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            process.kill()
            run.error = f"Timed out after {timeout:.0f}s."
            return run, {"other_tools": [], "server_status": None, "cost_usd": None}
        extra = parse_stream(stdout.decode("utf-8", "replace").splitlines(), run)
        if process.returncode and run.error is None and not run.report:
            run.error = f"claude exited {process.returncode}: {stderr.decode()[-300:]}"
        return run, extra


def summarize(outcomes: list[tuple[Run, dict, list[Result]]], model: str, server: str) -> str:
    passed = sum(all(r.passed for r in results) for _, _, results in outcomes)
    checks = [r for _, _, results in outcomes for r in results]
    cost = sum(e["cost_usd"] or 0 for _, e, _ in outcomes)
    lines = [
        f"## datalint headless eval: {passed} of {len(outcomes)} sessions passed",
        "",
        f"`{model}` in headless Claude Code, datalint server `{server}`. "
        f"{sum(r.passed for r in checks)} of {len(checks)} checks passed. "
        f"Cost reported by Claude Code: ${cost:.2f}.",
        "",
        "| Scenario | Result | Checks | datalint calls | Other tools | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for run, extra, results in outcomes:
        ok = sum(r.passed for r in results)
        lines.append(
            f"| {run.scenario} | {'pass' if ok == len(results) else 'FAIL'} | {ok}/{len(results)} "
            f"| {len(run.tool_calls)} | {len(extra['other_tools'])} "
            f"| {'-' if extra['cost_usd'] is None else '$%.2f' % extra['cost_usd']} |"
        )
    failures = [(run, r) for run, _, results in outcomes for r in results if not r.passed]
    if failures:
        lines += ["", "### Failed checks", ""]
        lines += [f"- **{run.scenario}**: {r.name} ({r.detail})" for run, r in failures]
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> list[tuple[Run, dict, list[Result]]]:
    scenarios = [BY_ID[s] for s in args.scenario] if args.scenario else SCENARIOS
    gate = asyncio.Semaphore(args.concurrency)

    async def job(scenario: Scenario, attempt: int):
        async with gate:
            started = time.monotonic()
            run, extra = await run_one(scenario, args.server, args.model, args.timeout)
            results = score(scenario, run)
            verdict = "pass" if all(r.passed for r in results) else "FAIL"
            print(f"  {scenario.id} #{attempt}: {verdict} ({len(run.tool_calls)} calls, "
                  f"{time.monotonic() - started:.0f}s)", file=sys.stderr)
            return run, extra, results

    return await asyncio.gather(*[job(s, i + 1) for s in scenarios for i in range(args.repeat)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.headless", description=__doc__.split("\n")[0])
    # Prefer the command installed next to this Python (an un-activated .venv), then PATH.
    default_server = (shutil.which("datalint-mcp", path=os.path.dirname(sys.executable))
                      or shutil.which("datalint-mcp") or "datalint-mcp")
    parser.add_argument("--server", default=default_server,
                        help="The datalint-mcp command to test. Default: the one installed "
                             "alongside this Python, else the one on PATH.")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--scenario", action="append", choices=sorted(BY_ID))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--out", type=Path, default=Path("evals/results"))
    args = parser.parse_args(argv)

    total = len(args.scenario or SCENARIOS) * args.repeat
    print(f"Running {total} headless session(s), {args.concurrency} at a time...", file=sys.stderr)
    outcomes = asyncio.run(main_async(args))
    summary = summarize(outcomes, args.model, args.server)
    print(summary)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (args.out / f"{stamp}-headless.json").write_text(json.dumps([
        {"scenario": run.scenario, "report": run.report, "error": run.error,
         "tool_calls": [{"name": c.name, "input": c.input, "is_error": c.is_error}
                        for c in run.tool_calls],
         "other_tools": extra["other_tools"], "cost_usd": extra["cost_usd"],
         "checks": [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in results]}
        for run, extra, results in outcomes
    ], indent=2), encoding="utf-8")
    (args.out / "latest-headless.md").write_text(summary, encoding="utf-8")
    return 0 if all(all(r.passed for r in res) for _, _, res in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
