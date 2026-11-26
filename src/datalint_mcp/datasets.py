"""Loading datasets, and the rules for which files the server may read."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from mcp.server.mcpserver.exceptions import ToolError

SUPPORTED_SUFFIXES = {".csv": ",", ".tsv": "\t"}
MAX_FILE_BYTES = 200 * 1024 * 1024


class DatasetError(ToolError):
    """A request the server refuses. The message says why and what to do instead.

    It is a ToolError so the MCP SDK passes the message to the model. Any other
    exception reaches the model only as a bare "Error executing tool".
    """


@dataclass
class Dataset:
    name: str
    path: Path
    frame: pd.DataFrame


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
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise DatasetError(
                f"{path.name} is not a .csv or .tsv file, the only formats supported so far."
            )
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise DatasetError(
                f"{path.name} is {size / 1024 / 1024:.0f} MB, over the "
                f"{MAX_FILE_BYTES // 1024 // 1024} MB limit."
            )
        return path

    def load(self, raw_path: str, name: str | None = None) -> Dataset:
        path = self.resolve(raw_path)
        frame = _read_table(path)
        dataset = Dataset(name=name or path.stem, path=path, frame=frame)
        self.datasets[dataset.name] = dataset
        return dataset

    def get(self, name: str) -> Dataset:
        if name in self.datasets:
            return self.datasets[name]
        loaded = ", ".join(sorted(self.datasets)) or "none yet"
        raise DatasetError(
            f"No dataset is loaded as {name!r}. Loaded: {loaded}. Call load_dataset first."
        )


def _read_table(path: Path) -> pd.DataFrame:
    # Only truly empty cells become missing. Text such as "N/A" or "null" is kept as
    # written, so check_missing can report it instead of pandas hiding it at load time.
    options = dict(sep=SUPPORTED_SUFFIXES[path.suffix.lower()], low_memory=False,
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


def roots_from_env() -> list[Path]:
    """Folders from DATALINT_ROOTS (separated like PATH), or the current folder."""
    raw = os.environ.get("DATALINT_ROOTS", "")
    parts = [p for p in raw.split(os.pathsep) if p.strip()] or [os.getcwd()]
    return [Path(p).expanduser().resolve() for p in parts]
