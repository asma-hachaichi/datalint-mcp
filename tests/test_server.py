"""The tools as an MCP client sees them: in-process, and over stdio like Claude Code runs it."""

import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from datalint_mcp import server

EXPECTED_TOOLS = {
    "load_dataset", "list_datasets", "profile_dataset",
    "describe_column", "check_missing", "find_duplicates",
}

pytestmark = pytest.mark.anyio


@pytest.fixture
def isolated_registry(data_dir, monkeypatch):
    monkeypatch.setattr(server.registry, "roots", [data_dir])
    monkeypatch.setattr(server.registry, "datasets", {})


def payload(result) -> dict:
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def test_lists_every_tool_as_read_only(isolated_registry):
    async with Client(server.mcp) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    assert all(t.description for t in tools)


async def test_load_then_profile(isolated_registry):
    async with Client(server.mcp) as client:
        loaded = payload(await client.call_tool("load_dataset", {"path": "customers.csv"}))
        profile = payload(await client.call_tool("profile_dataset", {"dataset": "customers"}))
    assert loaded["rows"] == 10
    assert profile["duplicate_rows"] == 2


async def test_refusal_reason_reaches_the_model(isolated_registry):
    async with Client(server.mcp) as client:
        result = await client.call_tool("load_dataset", {"path": "../customers.csv"})
    assert result.is_error
    assert "outside the folders" in result.content[0].text


async def test_bad_arguments_are_refused(isolated_registry):
    async with Client(server.mcp) as client:
        await client.call_tool("load_dataset", {"path": "customers.csv"})
        result = await client.call_tool(
            "profile_dataset", {"dataset": "customers", "limit": 0}
        )
    assert result.is_error


async def test_runs_over_stdio(data_dir):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "datalint_mcp"],
        env={**os.environ, "DATALINT_ROOTS": str(data_dir)},
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        loaded = payload(await client.call_tool("load_dataset", {"path": "customers.csv"}))
        dupes = payload(await client.call_tool("find_duplicates", {"dataset": "customers"}))
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert loaded["columns"] == 8
    assert dupes["groups"] == [{"row_numbers": [3, 9, 10], "copies": 3}]
