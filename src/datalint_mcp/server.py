"""The MCP server: tool definitions and descriptions the model reads."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, checks
from .datasets import Registry, roots_from_env

INSTRUCTIONS = """\
datalint audits the quality of tabular data.

Start with load_dataset, then profile_dataset for an overview of every column. Follow up with
check_missing, find_duplicates and describe_column on whatever looks wrong, then summarise the
problems for the user, most serious first, with the numbers the tools returned.

Results never contain whole rows. Duplicate checks return row numbers counted from 1 after the
header. Long lists are paged: when page.next_offset is not null, more items exist."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer("datalint", instructions=INSTRUCTIONS, version=__version__)
registry = Registry(roots=roots_from_env())

DatasetName = Annotated[str, Field(description="A dataset name returned by load_dataset.")]
Offset = Annotated[int, Field(ge=0, description="Index of the first item to return, for paging.")]


@mcp.tool(annotations=READ_ONLY)
def load_dataset(
    path: Annotated[str, Field(description=(
        "Path to a .csv or .tsv file, absolute or relative to the first allowed folder."
    ))],
    name: Annotated[str | None, Field(description=(
        "Name to use for it in the other tools. Defaults to the file name without extension."
    ))] = None,
) -> dict[str, Any]:
    """Load a CSV or TSV file so the other tools can audit it.

    Only truly empty cells count as missing. Placeholder text such as "N/A" or "null" is kept
    as written, so check_missing can report it. Loading under an existing name replaces it.
    Files outside the folders the user allowed are refused.
    """
    ds = registry.load(path, name)
    names = [str(c) for c in ds.frame.columns]
    return {
        "dataset": ds.name,
        "file": str(ds.path),
        "rows": len(ds.frame),
        "columns": len(names),
        "column_names": names[:100],
        "column_names_not_shown": max(0, len(names) - 100),
    }


@mcp.tool(annotations=READ_ONLY)
def list_datasets() -> dict[str, Any]:
    """List the datasets loaded in this session, with their size."""
    return {
        "datasets": [
            {"dataset": d.name, "file": str(d.path), "rows": d.frame.shape[0],
             "columns": d.frame.shape[1]}
            for d in registry.datasets.values()
        ]
    }


@mcp.tool(annotations=READ_ONLY)
def profile_dataset(
    dataset: DatasetName,
    offset: Offset = 0,
    limit: Annotated[int, Field(ge=1, le=200, description="Columns per page.")] = 50,
) -> dict[str, Any]:
    """Overview of a dataset: size, missing cells, duplicate rows, and one summary per column.

    Each column gets a type judged from its values: numeric, numeric_as_text (mostly numbers
    with some text mixed in, usually a data-entry problem), datetime, categorical, text,
    unique_text, identifier, boolean, constant or empty. disguised_missing counts placeholder
    text such as "N/A", "-" or blank spaces.
    """
    return checks.profile_dataset(registry.get(dataset), offset, limit)


@mcp.tool(annotations=READ_ONLY)
def describe_column(
    dataset: DatasetName,
    column: Annotated[str, Field(description="Exact column name, as profile_dataset lists it.")],
    top: Annotated[int, Field(ge=1, le=50, description="How many common values to list.")] = 10,
) -> dict[str, Any]:
    """A close look at one column.

    Numbers get min, quartiles, max, mean, zeros and negatives. Text gets lengths, values
    with stray spaces, and values that only differ by case or spacing. Dates get the earliest
    and latest. For numeric_as_text columns, lists the values that are not numbers. Common
    values are listed unless every value is unique.
    """
    return checks.describe_column(registry.get(dataset), column, top)


@mcp.tool(annotations=READ_ONLY)
def check_missing(
    dataset: DatasetName,
    offset: Offset = 0,
    limit: Annotated[int, Field(ge=1, le=200, description="Columns per page.")] = 50,
) -> dict[str, Any]:
    """Where values are missing, worst column first.

    Counts both empty cells and disguised ones (placeholder text such as "N/A", "null", "-" or
    blank spaces), and says which placeholders each column uses. Also counts rows with any
    missing value, fully empty rows, and columns with no values at all.
    """
    return checks.check_missing(registry.get(dataset), offset, limit)


@mcp.tool(annotations=READ_ONLY)
def find_duplicates(
    dataset: DatasetName,
    columns: Annotated[list[str] | None, Field(description=(
        "Compare only these columns, such as a key like ['email']. Omit to compare whole rows."
    ))] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Duplicate groups to list.")] = 10,
) -> dict[str, Any]:
    """Find duplicate rows, either whole rows or on chosen key columns.

    Returns how many extra copies exist, the largest groups as row numbers, and how many more
    rows match once case and spacing are ignored. Values are never shown.
    """
    return checks.find_duplicates(registry.get(dataset), columns, limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
