"""Release metadata stays consistent: one version everywhere, and what the registries require.

The release workflow also runs this with --release-tag, so a tag that doesn't match the
code's version can never publish.
"""

import json
import re
from pathlib import Path

import pytest

import datalint_mcp

ROOT = Path(__file__).resolve().parents[1]


def read_json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


# tomllib only exists from Python 3.11, and the package supports 3.10.
VERSION = re.search(r'^version = "([^"]+)"',
                    (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
SERVER = read_json("server.json")


def test_one_version_everywhere():
    server_package = SERVER["packages"][0]
    assert datalint_mcp.__version__ == VERSION
    assert read_json(".claude-plugin/plugin.json")["version"] == VERSION
    assert SERVER["version"] == VERSION
    assert server_package["version"] == VERSION
    # The plugin pins the exact release it was tested with.
    assert read_json(".mcp.json")["mcpServers"]["datalint"]["args"] == [f"datalint-mcp=={VERSION}"]


def test_mcp_registry_requirements():
    assert len(SERVER["description"]) <= 100
    assert SERVER["packages"][0]["identifier"] == "datalint-mcp"
    # The registry proves PyPI ownership through this line in the README (the PyPI description).
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(rf"mcp-name: {re.escape(SERVER['name'])}(\s|-->)", readme)


def test_plugin_and_marketplace_agree():
    marketplace = read_json(".claude-plugin/marketplace.json")
    assert marketplace["plugins"][0]["name"] == read_json(".claude-plugin/plugin.json")["name"]
    assert marketplace["plugins"][0]["source"] == "./"
    assert (ROOT / "skills" / "datalint" / "SKILL.md").is_file()


def test_release_tag_matches(request):
    tag = request.config.getoption("--release-tag")
    if tag is None:
        pytest.skip("Only checked when releasing (--release-tag).")
    assert tag == f"v{VERSION}", f"Tag {tag} does not match version {VERSION}"
