"""Run the evals: python -m evals [--model M] [--scenario ID ...] [--repeat N]

Needs Claude API credentials (ANTHROPIC_API_KEY, or an `ant auth login` profile) and the
eval extra: pip install -e ".[eval]". Every run calls the Claude API and costs money; the
summary reports what each run cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .runner import run_scenario
from .scenarios import BY_ID, SCENARIOS
from .scoring import Result, Run, cost_usd, score


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--effort", default="medium",
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--scenario", action="append", choices=sorted(BY_ID),
                        help="Run only this scenario. Repeat to pick several. Default: all.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per scenario.")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("evals/results"))
    parser.add_argument("--min-pass-rate", type=float, default=None,
                        help="Exit with an error if fewer runs than this share pass (0 to 1).")
    return parser.parse_args(argv)


async def run_all(args: argparse.Namespace) -> list[tuple[Run, list[Result]]]:
    import anthropic  # only needed for real runs, so the tests don't depend on it

    llm = anthropic.AsyncAnthropic()
    scenarios = [BY_ID[s] for s in args.scenario] if args.scenario else SCENARIOS
    gate = asyncio.Semaphore(args.concurrency)

    async def one(scenario):
        async with gate:
            run = await run_scenario(llm, scenario, args.model, args.effort)
            print(f"  {scenario.id}: done ({len(run.tool_calls)} tool calls)", file=sys.stderr)
            return run, score(scenario, run)

    jobs = [one(s) for s in scenarios for _ in range(args.repeat)]
    return await asyncio.gather(*jobs)


def summarize(outcomes: list[tuple[Run, list[Result]]], model: str, effort: str) -> str:
    passed_runs = sum(all(r.passed for r in results) for _, results in outcomes)
    checks = [r for _, results in outcomes for r in results]
    costs = [cost_usd(run) for run, _ in outcomes]
    total_cost = sum(c for c in costs if c is not None)
    lines = [
        f"## datalint eval: {passed_runs} of {len(outcomes)} runs passed",
        "",
        f"Model `{model}` at effort `{effort}`. "
        f"{sum(r.passed for r in checks)} of {len(checks)} checks passed. "
        + (f"Cost: ${total_cost:.2f}." if any(c is not None for c in costs)
           else "Cost unknown for this model."),
        "",
        "| Scenario | Result | Checks | Tool calls | Turns | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for (run, results), cost in zip(outcomes, costs):
        ok = sum(r.passed for r in results)
        lines.append(
            f"| {run.scenario} | {'pass' if ok == len(results) else 'FAIL'} | "
            f"{ok}/{len(results)} | {len(run.tool_calls)} | {run.turns} | "
            f"{'-' if cost is None else f'${cost:.3f}'} |"
        )
    failures = [(run, r) for run, results in outcomes for r in results if not r.passed]
    if failures:
        lines += ["", "### Failed checks", ""]
        lines += [f"- **{run.scenario}**: {r.name} ({r.detail})" for run, r in failures]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    count = len(args.scenario or SCENARIOS) * args.repeat
    print(f"Running {count} eval run(s) on {args.model}...", file=sys.stderr)
    outcomes = asyncio.run(run_all(args))
    summary = summarize(outcomes, args.model, args.effort)
    print(summary)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    record = {
        "model": args.model,
        "effort": args.effort,
        "runs": [{"run": asdict(run), "checks": [asdict(r) for r in results],
                  "cost_usd": cost_usd(run)} for run, results in outcomes],
    }
    (args.out / f"{stamp}-{args.model}.json").write_text(json.dumps(record, indent=2),
                                                         encoding="utf-8")
    (args.out / "latest.md").write_text(summary, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)

    pass_rate = sum(all(r.passed for r in res) for _, res in outcomes) / max(1, len(outcomes))
    if args.min_pass_rate is not None and pass_rate < args.min_pass_rate:
        print(f"Pass rate {pass_rate:.0%} is below {args.min_pass_rate:.0%}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
