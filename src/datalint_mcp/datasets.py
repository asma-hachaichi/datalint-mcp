"""Loading datasets, and the rules for which files the server may read."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from mcp.server.mcpserver.exceptions import ToolError

TEXT_SUFFIXES = {".csv": ",", ".tsv": "\t"}
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TABLES = 50


class DatasetError(ToolError):
    """A request the server refuses. The message says why and what to do instead.

    It is a ToolError so the MCP SDK passes the message to the model. Any other
    exception reaches the model only as a bare "Error executing tool".
    """


@dataclass(frozen=True)
class ForeignKey:
    """A link a SQLite database declares: this column points at a column of another table."""

    column: str
    ref_table: str
    ref_column: str


@dataclass
class Dataset:
    name: str
    path: Path
    frame: pd.DataFrame
    table: str | None = None
    foreign_keys: list[ForeignKey] = field(default_factory=list)


@dataclass
class Registry:
    """The datasets loaded in this session, and the folders the server may read from."""

    roots: list[Path]
    datasets: dict[str, Dataset] = field(default_factory=dict)

    def resolve(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.roots[0] / path
        path = path.resolve()

        if not any(path.is_relative_to(root) for root in self.roots):
            allowed = ", ".join(str(r) for r in self.roots)
            raise DatasetError(
                f"{raw_path} is outside the folders this server may read ({allowed}). "
                "Ask the user to add its folder to DATALINT_ROOTS."
            )
        if not path.is_file():
            raise DatasetError(f"There is no file at {path}.")
        suffix = path.suffix.lower()
        if suffix not in TEXT_SUFFIXES and suffix not in SQLITE_SUFFIXES:
            raise DatasetError(
                f"{path.name} is not a supported file. Supported: .csv, .tsv, and SQLite "
                "databases (.db, .sqlite, .sqlite3)."
            )
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise DatasetError(
                f"{path.name} is {size / 1024 / 1024:.0f} MB, over the "
                f"{MAX_FILE_BYTES // 1024 // 1024} MB limit."
            )
        return path

    def load(self, raw_path: str, name: str | None = None,
             table: str | None = None) -> list[Dataset]:
        """Load a file. A SQLite file loads every table unless `table` names one."""
        path = self.resolve(raw_path)
        if path.suffix.lower() in TEXT_SUFFIXES:
            if table:
                raise DatasetError("table only applies to SQLite databases, not CSV or TSV files.")
            loaded = [Dataset(name=name or path.stem, path=path, frame=_read_text(path))]
        else:
            loaded = _read_sqlite(path, table, name)
        for dataset in loaded:
            self.datasets[dataset.name] = dataset
        return loaded

    def get(self, name: str) -> Dataset:
        if name in self.datasets:
            return self.datasets[name]
        loaded = ", ".join(sorted(self.datasets)) or "none yet"
        raise DatasetError(
            f"No dataset is loaded as {name!r}. Loaded: {loaded}. Call load_dataset first."
        )


def _read_text(path: Path) -> pd.DataFrame:
    # Only truly empty cells become missing. Text such as "N/A" or "null" is kept as
    # written, so check_missing can report it instead of pandas hiding it at load time.
    options = dict(sep=TEXT_SUFFIXES[path.suffix.lower()], low_memory=False,
                   keep_default_na=False, na_values=[""])
    try:
        try:
            return pd.read_csv(path, **options)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="latin-1", **options)
    except pd.errors.EmptyDataError:
        raise DatasetError(f"{path.name} is empty.") from None
    except pd.errors.ParserError as exc:
        raise DatasetError(f"{path.name} could not be parsed as a table: {exc}") from None


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _read_sqlite(path: Path, table: str | None, name: str | None) -> list[Dataset]:
    # Read-only, so auditing a database can never change it.
    uri = f"{path.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as con:
            tables = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            if not tables:
                raise DatasetError(f"{path.name} has no tables.")
            if table is not None and table not in tables:
                raise DatasetError(
                    f"{path.name} has no table {table!r}. Its tables: {', '.join(tables)}."
                )
            wanted = [table] if table else tables
            if name and len(wanted) > 1:
                raise DatasetError(
                    "name only works when loading one table. Pass table as well, or omit name "
                    "to load every table under its own name."
                )
            if len(wanted) > MAX_TABLES:
                raise DatasetError(
                    f"{path.name} has {len(wanted)} tables, over the limit of {MAX_TABLES}. "
                    "Pass table to load them one at a time."
                )
            return [
                Dataset(
                    name=name or t,
                    path=path,
                    frame=pd.read_sql_query(f"SELECT * FROM {_quote(t)}", con),
                    table=t,
                    foreign_keys=_foreign_keys(con, t),
                )
                for t in wanted
            ]
    except sqlite3.DatabaseError as exc:
        raise DatasetError(f"{path.name} could not be read as a SQLite database: {exc}") from None


def _foreign_keys(con: sqlite3.Connection, table: str) -> list[ForeignKey]:
    keys = []
    for row in con.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
        ref_table, column, ref_column = row[2], row[3], row[4]
        if ref_column is None:  # REFERENCES parent with no column means its primary key
            primary = [r[1] for r in con.execute(f"PRAGMA table_info({_quote(ref_table)})")
                       if r[5] == 1]
            if len(primary) != 1:
                continue
            ref_column = primary[0]
        keys.append(ForeignKey(column=column, ref_table=ref_table, ref_column=ref_column))
    return keys


def roots_from_env() -> list[Path]:
    """Folders from DATALINT_ROOTS (separated like PATH), or the current folder."""
    raw = os.environ.get("DATALINT_ROOTS", "")
    parts = [p for p in raw.split(os.pathsep) if p.strip()] or [os.getcwd()]
    return [Path(p).expanduser().resolve() for p in parts]
