"""The eval harness, tested without calling the Claude API.

A scripted stand-in for the model drives the real MCP server over stdio, so the loop,
tool plumbing and scoring are all exercised for real.
"""

from types import SimpleNamespace as NS

import pytest

from evals import scenarios as sc
from evals.__main__ import summarize
from evals.runner import run_scenario, system_prompt
from evals.scoring import Run, ToolCall, cost_usd, score

pytestmark = pytest.mark.anyio


def text(t):
    return NS(type="text", text=t)


def tool(id_, name, **args):
    return NS(type="tool_use", id=id_, name=name, input=args)


def reply(*blocks, stop="tool_use"):
    usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=500,
               cache_creation_input_tokens=0)
    return NS(content=list(blocks), stop_reason=stop, usage=usage)


class ScriptedModel:
    """Plays back fixed replies and records every request it receives."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    async def create(self, **request):
        self.requests.append(request)
        return self.replies.pop(0)


# ------------------------------------------------------------ the runner


async def test_good_audit_passes_every_check():
    model = ScriptedModel(
        reply(tool("t1", "load_dataset", path="sensors.csv"),
              tool("t2", "find_outliers", dataset="sensors", column="temperature_c")),
        reply(text("One reading is impossible: row 37 records 999 °C. Remove it before "
                   "charting."), stop="end_turn"),
    )
    run = await run_scenario(model, sc.OUTLIER, "claude-opus-5-5")
    assert run.error is None
    assert [c.name for c in run.tool_calls] == ["load_dataset", "find_outliers"]
    assert '"row_number": 37' in run.tool_calls[1].result.replace("'", '"')
    assert all(r.passed for r in score(sc.OUTLIER, run))


async def test_requests_follow_the_api_rules():
    model = ScriptedModel(
        reply(tool("t1", "load_dataset", path="sensors.csv")),
        reply(text("Row 37 is 999."), stop="end_turn"),
    )
    await run_scenario(model, sc.OUTLIER, "claude-opus-5-5", effort="high")
    first, second = model.requests
    assert first["output_config"] == {"effort": "high"}
    assert {t["name"] for t in first["tools"]} >= {"load_dataset", "find_outliers"}
    # The assistant turn goes back whole, then every tool result in one user message.
    assistant, results = second["messages"][1], second["messages"][2]
    assert assistant["role"] == "assistant"
    assert results == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": results["content"][0]["content"],
         "is_error": False}
    ]}


async def test_tool_errors_reach_the_model_as_errors():
    model = ScriptedModel(
        reply(tool("t1", "profile_dataset", dataset="never_loaded")),
        reply(text("I need to load it first."), stop="end_turn"),
    )
    run = await run_scenario(model, sc.OUTLIER, "claude-opus-5-5")
    assert run.tool_calls[0].is_error
    assert "load_dataset first" in run.tool_calls[0].result
    assert model.requests[1]["messages"][2]["content"][0]["is_error"] is True


async def test_endless_tool_use_is_cut_off():
    model = ScriptedModel(*[reply(tool(f"t{i}", "list_datasets")) for i in range(3)])
    run = await run_scenario(model, sc.OUTLIER, "claude-opus-5-5", max_turns=3)
    assert run.error == "No final report after 3 turns."
    assert not score(sc.OUTLIER, run)[0].passed


async def test_personal_values_never_reach_the_transcript():
    model = ScriptedModel(
        reply(tool("t1", "load_dataset", path="contacts.csv"),
              tool("t2", "describe_column", dataset="contacts", column="email")),
        reply(text("The email, phone and full_name columns hold personal data."), stop="end_turn"),
    )
    run = await run_scenario(model, sc.PERSONAL, "claude-opus-5-5")
    leak = next(r for r in score(sc.PERSONAL, run) if r.name == "no personal data leaked")
    assert leak.passed


# ------------------------------------------------------------ scoring


def finished(scenario, report, *tool_names):
    return Run(scenario=scenario.id, model="claude-opus-5-5", report=report,
               stop_reason="end_turn",
               tool_calls=[ToolCall(n, {}, "{}", False) for n in tool_names])


def failed_names(scenario, run):
    return [r.name for r in score(scenario, run) if not r.passed]


def test_a_vague_report_fails_the_content_checks():
    run = finished(sc.ORPHANS, "The data looks mostly fine.", "check_references")
    assert failed_names(sc.ORPHANS, run) == [
        "counts the 4 orphaned orders", "points at an orphaned row",
    ]


def test_a_precise_report_passes():
    run = finished(sc.ORPHANS, "4 orders (rows 7, 19, 33 and 48) point at customers that "
                               "don't exist.", "check_references")
    assert failed_names(sc.ORPHANS, run) == []


@pytest.mark.parametrize("scenario,report,tool_name", [
    # Correct reports from real headless runs that an earlier, narrower check rejected.
    (sc.DUPLICATES, "Three customers are copied exactly: rows 41, 42 and 43 match rows 5, 12 "
                    "and 30. Two more differ only by case and spacing.", "find_duplicates"),
    (sc.BAD_KEY, "No, `line_id` can't be the primary key. Rows 17 and 31 have the same "
                 "`line_id`.", "check_keys"),
    (sc.DUPLICATES, "Three customers appear twice, word for word. Two more differ only by "
                    "case and spacing.", "find_duplicates"),
], ids=["copied exactly", "same line_id", "appear twice"])
def test_correct_wordings_from_real_runs_pass(scenario, report, tool_name):
    assert failed_names(scenario, finished(scenario, report, tool_name)) == []


def test_skipping_the_required_tool_fails():
    run = finished(sc.ORPHANS, "4 orders point at customers that don't exist (row 19).")
    assert failed_names(sc.ORPHANS, run) == ["called check_references"]


def test_inventing_problems_in_clean_data_fails():
    run = finished(sc.CLEAN, "Not ready: 3 duplicate rows and 2 outliers.", "profile_dataset")
    assert set(failed_names(sc.CLEAN, run)) == {
        "gives a clean verdict", "does not: invents duplicate rows", "does not: invents outliers",
    }
    ok = finished(sc.CLEAN, "Ready to use: 0 duplicate rows and no outliers.", "profile_dataset")
    assert failed_names(sc.CLEAN, ok) == []


def test_a_leaked_value_fails():
    secret = sc.PERSONAL.secrets[0]
    run = finished(sc.PERSONAL, f"Personal data: email (e.g. {secret}), phone, full_name.",
                   "detect_pii")
    assert failed_names(sc.PERSONAL, run) == ["no personal data leaked"]


def test_too_many_tool_calls_fails():
    run = finished(sc.CLEAN, "Ready to use.", *["profile_dataset"] * 9)
    assert failed_names(sc.CLEAN, run) == ["used at most 8 tool calls"]


def test_a_refusal_does_not_count_as_finished():
    run = finished(sc.CLEAN, "Ready.", "profile_dataset")
    run.stop_reason = "refusal"
    assert "finished with a report" in failed_names(sc.CLEAN, run)


def test_cost_and_summary():
    run = finished(sc.CLEAN, "Ready to use.", "profile_dataset")
    run.input_tokens, run.output_tokens, run.cache_read_tokens = 1_000_000, 100_000, 1_000_000
    assert cost_usd(run) == pytest.approx(4.00 + 2.00 + 0.20)
    assert cost_usd(Run(scenario="x", model="some-other-model")) is None
    summary = summarize([(run, score(sc.CLEAN, run))], "claude-opus-5-5", "medium")
    assert "1 of 1 runs passed" in summary
    assert "| clean_data | pass |" in summary


def test_system_prompt_is_the_skill_without_frontmatter():
    prompt = system_prompt()
    assert prompt.startswith("# Auditing data with datalint")
    assert "Server instructions" in prompt
