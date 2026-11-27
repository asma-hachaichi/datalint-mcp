"""The MCP server: tool definitions and descriptions the model reads."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, checks, keys
from .datasets import Registry, roots_from_env

INSTRUCTIONS = """\
datalint audits the quality of tabular data: CSV and TSV files, and SQLite databases.

Start with load_dataset (a database loads every table), then profile_dataset for an overview of
every column, including which ones hold personal data. Follow up with check_missing,
find_duplicates, find_outliers and describe_column on whatever looks wrong. For several related
tables, use check_keys and check_references. Then summarise the problems for the user, most
serious first, with the numbers the tools returned.

Results never contain whole rows, and values in columns that look like personal data are never
shown. Row numbers count data rows from 1. Long lists are paged: when page.next_offset is not
null, more items exist."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer("datalint", instructions=INSTRUCTIONS, version=__version__)
registry = Registry(roots=roots_from_env())

DatasetName = Annotated[str, Field(description="A dataset name returned by load_dataset.")]
Offset = Annotated[int, Field(ge=0, description="Index of the first item to return, for paging.")]


@mcp.tool(annotations=READ_ONLY)
def load_dataset(
    path: Annotated[str, Field(description=(
        "Path to a .csv, .tsv or SQLite (.db, .sqlite, .sqlite3) file, absolute or relative "
        "to the first allowed folder."
    ))],
    name: Annotated[str | None, Field(description=(
        "Name to use in the other tools. Defaults to the file name, or for a database the "
        "table name. Only allowed when loading a single table."
    ))] = None,
    table: Annotated[str | None, Field(description=(
        "SQLite only: load just this table. Omit to load every table."
    ))] = None,
) -> dict[str, Any]:
    """Load a file so the other tools can audit it. A SQLite database loads every table, each
    as its own dataset named after the table, along with the foreign keys it declares.

    Only truly empty cells count as missing. Placeholder text such as "N/A" or "null" is kept
    as written, so check_missing can report it. Loading under an existing name replaces it.
    Databases are opened read-only. Files outside the folders the user allowed are refused.
    """
    loaded = registry.load(path, name, table)
    datasets = []
    for ds in loaded:
        names = [str(c) for c in ds.frame.columns]
        datasets.append({
            "dataset": ds.name,
            "rows": len(ds.frame),
            "columns": len(names),
            "column_names": names[:100],
            "column_names_not_shown": max(0, len(names) - 100),
            "declared_foreign_keys": [
                f"{fk.column} -> {fk.ref_table}.{fk.ref_column}" for fk in ds.foreign_keys
            ],
        })
    return {"file": str(loaded[0].path), "datasets": datasets}


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


@mcp.tool(annotations=READ_ONLY)
def find_outliers(
    dataset: DatasetName,
    column: Annotated[str | None, Field(description=(
        "One numeric column to examine closely. Omit to summarise every numeric column."
    ))] = None,
    method: Annotated[Literal["iqr", "zscore"], Field(description=(
        "iqr (default): beyond 1.5 interquartile ranges from the middle half, robust to skew. "
        "zscore: more than 3 standard deviations from the mean, for bell-shaped data."
    ))] = "iqr",
    limit: Annotated[int, Field(ge=1, le=100, description=(
        "Columns per page, or with column, how many of the most extreme rows to list."
    ))] = 10,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Find numbers far outside a column's usual range.

    Without column: every numeric column with its outlier count and usual range, most outliers
    first. With column: also its most extreme rows as row numbers with values (values hidden if
    the column looks like personal data).
    """
    return checks.find_outliers(registry.get(dataset), column, method, limit, offset)


@mcp.tool(annotations=READ_ONLY)
def check_keys(
    dataset: DatasetName,
    columns: Annotated[list[str] | None, Field(description=(
        "Check whether these columns together uniquely identify every row, such as ['id'] or "
        "['order_id', 'line']. Omit to list candidate keys and check every id-like column."
    ))] = None,
) -> dict[str, Any]:
    """Check whether a column (or set of columns) works as a unique key: no missing values and
    no repeats. Reports how many rows break it, with example row numbers."""
    return keys.check_keys(registry.get(dataset), columns)


@mcp.tool(annotations=READ_ONLY)
def check_references(
    dataset: DatasetName,
    column: Annotated[str | None, Field(description=(
        "Column in this dataset that points at another dataset, such as 'customer_id'."
    ))] = None,
    references: Annotated[str | None, Field(description=(
        "What it points at, as 'dataset.column', such as 'customers.id'. Pass with column."
    ))] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Orphan row numbers to list.")] = 10,
) -> dict[str, Any]:
    """Find orphans: values that point at rows missing from the referenced dataset, such as
    orders whose customer does not exist.

    Without column and references, checks every foreign key the database declares, plus links
    guessed from column names across loaded datasets (orders.customer_id -> customers.id).
    """
    return keys.check_references(registry, registry.get(dataset), column, references, limit)


@mcp.tool(annotations=READ_ONLY)
def detect_pii(
    dataset: DatasetName,
    columns: Annotated[list[str] | None, Field(description=(
        "Columns to scan. Omit to scan every column."
    ))] = None,
    offset: Offset = 0,
    limit: Annotated[int, Field(ge=1, le=200, description="Columns per page.")] = 50,
) -> dict[str, Any]:
    """Find columns that look like personal data: emails, phone numbers, card numbers, IBANs,
    IP addresses, US social security numbers, and names, addresses, birth dates or ID numbers
    suggested by the column name.

    Reports which kinds, how many values match, and whether the evidence is the values or only
    the column name. Never returns the values themselves.
    """
    return checks.detect_pii(registry.get(dataset), columns, offset, limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
