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
PARQUET_SUFFIXES = {".parquet", ".pq"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES.keys() | SQLITE_SUFFIXES | PARQUET_SUFFIXES | EXCEL_SUFFIXES
SUPPORTED_DESCRIPTION = (
    "CSV and TSV files, Excel workbooks (.xlsx, .xlsm), Parquet files (.parquet, .pq) and "
    "SQLite databases (.db, .sqlite, .sqlite3)"
)
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TABLES = 50
# Parquet compresses well, so a file under the size limit can still expand to far more rows
# than fit in memory. Its row count is stored up front, so this is checked before loading.
MAX_PARQUET_ROWS = 5_000_000


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
        if suffix == ".xls":
            raise DatasetError(
                f"{path.name} is in the old Excel 97-2003 format, which isn't supported. Ask "
                "the user to save it as .xlsx."
            )
        if suffix not in SUPPORTED_SUFFIXES:
            raise DatasetError(
                f"{path.name} is not a supported file. Supported: {SUPPORTED_DESCRIPTION}."
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
        """Load a file. A SQLite database loads every table, and an Excel workbook every
        sheet, unless `table` names one."""
        path = self.resolve(raw_path)
        suffix = path.suffix.lower()
        if suffix in SQLITE_SUFFIXES:
            loaded = _read_sqlite(path, table, name)
        elif suffix in EXCEL_SUFFIXES:
            loaded = _read_excel(path, table, name)
        else:
            if table:
                raise DatasetError(
                    "table only applies to SQLite tables and Excel sheets, not to "
                    f"{path.suffix} files."
                )
            reader = _read_parquet if suffix in PARQUET_SUFFIXES else _read_text
            loaded = [Dataset(name=name or path.stem, path=path, frame=reader(path))]
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


def _read_parquet(path: Path) -> pd.DataFrame:
    import pyarrow.parquet as pq
    from pyarrow import ArrowException

    try:
        rows = pq.ParquetFile(path).metadata.num_rows
        if rows > MAX_PARQUET_ROWS:
            raise DatasetError(
                f"{path.name} has {rows:,} rows, over the limit of {MAX_PARQUET_ROWS:,}."
            )
        return pd.read_parquet(path)
    except (ArrowException, OSError) as exc:
        raise DatasetError(f"{path.name} could not be read as a Parquet file: {exc}") from None


def _read_excel(path: Path, sheet: str | None, name: str | None) -> list[Dataset]:
    import zipfile

    try:
        with pd.ExcelFile(path, engine="openpyxl") as workbook:
            wanted = _pick(workbook.sheet_names, sheet, name, path, "sheet")
            # Same rule as CSV: only empty cells are missing, so "N/A" stays visible.
            return [
                Dataset(
                    name=name or s,
                    path=path,
                    frame=pd.read_excel(workbook, sheet_name=s, keep_default_na=False,
                                        na_values=[""]),
                    table=s,
                )
                for s in wanted
            ]
    except DatasetError:
        raise
    except (zipfile.BadZipFile, ValueError, KeyError, OSError) as exc:
        raise DatasetError(f"{path.name} could not be read as an Excel workbook: {exc}") from None


def _pick(available: list[str], wanted: str | None, name: str | None, path: Path,
          kind: str) -> list[str]:
    """Which tables or sheets to load, with refusals that name the real choices."""
    if not available:
        raise DatasetError(f"{path.name} has no {kind}s.")
    if wanted is not None and wanted not in available:
        raise DatasetError(
            f"{path.name} has no {kind} {wanted!r}. Its {kind}s: {', '.join(available)}."
        )
    chosen = [wanted] if wanted else list(available)
    if name and len(chosen) > 1:
        raise DatasetError(
            f"name only works when loading one {kind}. Pass table as well, or omit name to "
            f"load every {kind} under its own name."
        )
    if len(chosen) > MAX_TABLES:
        raise DatasetError(
            f"{path.name} has {len(chosen)} {kind}s, over the limit of {MAX_TABLES}. "
            f"Pass table to load them one at a time."
        )
    return chosen


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
            wanted = _pick(tables, table, name, path, "table")
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
