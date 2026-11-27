"""Key integrity: is a column a usable unique key, and do references between tables hold?"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from . import pii
from .checks import ROW_NUMBER_NOTE, disguised_missing_mask, get_column, infer_type, page, pct
from .datasets import Dataset, DatasetError, Registry

MAX_KEY_TEXT_LENGTH = 64  # longer text is prose, not a key
ID_NAME = re.compile(r"(^id$|_id$|^id_|^key$|_key$|^uuid$|_uuid$|^code$|_code$)", re.I)


def _key_text(s: pd.Series) -> pd.Series:
    """Values as comparable text, so 7, 7.0 and " 7" all become "7". Missing stays missing."""
    if pd.api.types.is_numeric_dtype(s):
        def render(v: Any) -> Any:
            if pd.isna(v):
                return None
            v = float(v)
            return str(int(v)) if v.is_integer() else repr(v)
        return s.map(render)
    text = s.astype(str).str.strip()
    return text.where(s.notna() & ~disguised_missing_mask(s))


def _evaluate_key(ds: Dataset, columns: list[str], limit: int) -> dict:
    keys = pd.DataFrame({c: _key_text(get_column(ds, c)) for c in columns})
    missing = keys.isna().any(axis=1)
    complete = keys[~missing]
    duplicated = complete.duplicated(keep=False)
    groups = (
        pd.Series(np.flatnonzero(~missing.to_numpy())[duplicated.to_numpy()])
        .groupby(pd.util.hash_pandas_object(complete[duplicated], index=False).to_numpy())
        .agg(list)
    )
    groups = sorted(groups, key=lambda g: (-len(g), g[0]))
    return {
        "key": columns,
        "rows": len(keys),
        "rows_missing_a_key_value": int(missing.sum()),
        "duplicate_rows": int(complete.duplicated().sum()),
        "duplicate_groups": len(groups),
        "example_duplicate_row_numbers": [[int(i) + 1 for i in g] for g in groups[:limit]],
        "is_valid_key": not missing.any() and not duplicated.any(),
    }


def check_keys(ds: Dataset, columns: list[str] | None = None, limit: int = 5) -> dict:
    if columns:
        return {"dataset": ds.name, **_evaluate_key(ds, columns, limit),
                "notes": [ROW_NUMBER_NOTE]}

    candidates, id_like = [], []
    for name in ds.frame.columns:
        name = str(name)
        s = ds.frame[name]
        keys = _key_text(s)
        complete_and_unique = len(s) > 0 and keys.notna().all() and keys.is_unique
        too_long = not pd.api.types.is_numeric_dtype(s) and keys.str.len().mean() > MAX_KEY_TEXT_LENGTH
        if (complete_and_unique and not too_long
                and not pd.api.types.is_float_dtype(s) and infer_type(s) != "constant"):
            candidates.append(name)
        if ID_NAME.search(name):
            id_like.append({"column": name, **{
                k: v for k, v in _evaluate_key(ds, [name], limit).items() if k != "key"
            }})
    return {
        "dataset": ds.name,
        "candidate_keys": candidates,
        "id_like_columns": id_like,
        "notes": [
            "candidate_keys are columns with no missing and no repeated values. In a small "
            "table, many columns qualify by chance.",
            "Pass columns to check one key, including a key made of several columns.",
            ROW_NUMBER_NOTE,
        ],
    }


def _singular(name: str) -> str:
    name = name.lower()
    if name.endswith("ies"):
        return name[:-3] + "y"
    return name[:-1] if name.endswith("s") else name


def _check_reference(child: Dataset, column: str, parent: Dataset, parent_column: str,
                     basis: str, limit: int) -> dict:
    child_values = get_column(child, column)
    child_keys = _key_text(child_values)
    parent_keys = _key_text(get_column(parent, parent_column))
    present = child_keys.notna()
    orphan = present & ~child_keys.isin(set(parent_keys.dropna()))
    positions = np.flatnonzero(orphan.to_numpy())
    result: dict[str, Any] = {
        "column": column,
        "references": f"{parent.name}.{parent_column}",
        "basis": basis,
        "rows_checked": int(present.sum()),
        "rows_missing_a_value": int((~present).sum()),
        "orphans": len(positions),
        "orphans_pct": pct(len(positions), int(present.sum())),
        "distinct_orphan_values": int(child_keys[orphan].nunique()),
        "orphan_row_numbers": [int(i) + 1 for i in positions[:limit]],
        "referenced_column_is_unique": bool(parent_keys.notna().all() and parent_keys.is_unique),
    }
    if len(positions) and not pii.personal_kinds(child_values, column):
        result["orphan_examples"] = list(child_keys[orphan].unique()[:5])
    return result


def check_references(registry: Registry, ds: Dataset, column: str | None = None,
                     references: str | None = None, limit: int = 10) -> dict:
    checks: list[dict] = []
    if column or references:
        if not (column and references):
            raise DatasetError(
                "Pass both column and references (as 'dataset.column'), or neither to check "
                "the relationships the data declares or suggests."
            )
        parent_name, _, parent_column = references.rpartition(".")
        if not parent_name:
            raise DatasetError(
                f"references must look like 'dataset.column', such as 'customers.id', "
                f"not {references!r}."
            )
        parent = registry.get(parent_name)
        checks.append(_check_reference(ds, column, parent, parent_column, "requested", limit))
    else:
        seen = set()
        for fk in ds.foreign_keys:
            parent = next((d for d in registry.datasets.values()
                           if d.path == ds.path and d.table == fk.ref_table), None)
            if parent is None:
                checks.append({
                    "column": fk.column,
                    "references": f"{fk.ref_table}.{fk.ref_column}",
                    "basis": "declared",
                    "skipped": f"Table {fk.ref_table!r} is not loaded. Load it with load_dataset.",
                })
                continue
            seen.add((fk.column, parent.name, fk.ref_column))
            checks.append(_check_reference(ds, fk.column, parent, fk.ref_column,
                                           "declared", limit))
        for child_col, parent, parent_col, basis in _suggested(registry, ds):
            if (child_col, parent.name, parent_col) not in seen:
                checks.append(_check_reference(ds, child_col, parent, parent_col, basis, limit))

    return {
        "dataset": ds.name,
        "relationships_checked": sum(1 for c in checks if "skipped" not in c),
        "relationships_with_orphans": sum(1 for c in checks if c.get("orphans")),
        "relationships": checks,
        "notes": [
            "An orphan is a value that points at something missing from the referenced table.",
            "basis says why a relationship was checked: declared by the database, requested, "
            "or guessed from column names (same_column_name, name_pattern).",
            ROW_NUMBER_NOTE,
        ] + ([] if checks else [
            "No relationship was declared or guessed. Pass column and references to check one."
        ]),
    }


def _suggested(registry: Registry, ds: Dataset) -> list[tuple[str, Dataset, str, str]]:
    """Guess relationships by name: orders.customer_id -> customers.id or customers.customer_id."""
    guesses = []
    for parent in registry.datasets.values():
        if parent is ds:
            continue
        parent_columns = {str(c).lower(): str(c) for c in parent.frame.columns}
        for column in (str(c) for c in ds.frame.columns):
            lowered = column.lower()
            if not ID_NAME.search(column) or lowered == "id":
                continue
            if lowered in parent_columns:
                guesses.append((column, parent, parent_columns[lowered], "same_column_name"))
            elif lowered == f"{_singular(parent.name)}_id" and "id" in parent_columns:
                guesses.append((column, parent, parent_columns["id"], "name_pattern"))
    return guesses
