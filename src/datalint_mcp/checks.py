"""The data quality checks. Each takes a Dataset and returns plain, JSON-ready data."""

from __future__ import annotations

import difflib
import math
import warnings
from typing import Any

import numpy as np
import pandas as pd

from . import pii
from .datasets import Dataset, DatasetError

MAX_TEXT_CHARS = 80
TYPE_SAMPLE_SIZE = 500
TYPE_MATCH_SHARE = 0.9
ROW_NUMBER_NOTE = "Row numbers count data rows from 1: row 1 is the first line after the header."

# Text that people type when a value is missing. Compared after trimming and lowercasing,
# so "" also catches cells that hold only spaces.
DISGUISED_MISSING = {
    "", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "?", "unknown", "missing", "#n/a",
}


# ---------------------------------------------------------------- shared helpers


def to_json(value: Any) -> Any:
    """Turn a pandas or numpy value into something JSON can hold, clipping long text."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 4)
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "…"
    return value


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def page(items: list, offset: int, limit: int) -> tuple[list, dict]:
    total = len(items)
    end = offset + limit
    return items[offset:end], {
        "offset": offset,
        "limit": limit,
        "total": total,
        "next_offset": end if end < total else None,
    }


def get_column(ds: Dataset, column: str) -> pd.Series:
    if column in ds.frame.columns:
        return ds.frame[column]
    names = [str(c) for c in ds.frame.columns]
    close = difflib.get_close_matches(column, names, n=3)
    hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
    raise DatasetError(
        f"{ds.name} has no column {column!r}.{hint} Call profile_dataset to list every column."
    )


def is_text(s: pd.Series) -> bool:
    return not (
        pd.api.types.is_numeric_dtype(s)
        or pd.api.types.is_bool_dtype(s)
        or pd.api.types.is_datetime64_any_dtype(s)
    )


def disguised_missing_mask(s: pd.Series) -> pd.Series:
    """True where a text cell holds a placeholder like "N/A" instead of a value."""
    if not is_text(s):
        return pd.Series(False, index=s.index)
    return s.notna() & s.astype(str).str.strip().str.lower().isin(DISGUISED_MISSING)


def normalize_text(s: pd.Series) -> pd.Series:
    """Lowercase, trim and collapse spaces, so "  Paris" and "paris" compare equal."""
    if not is_text(s):
        return s
    cleaned = s.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    return cleaned.where(s.notna())


def _datetime_share(values: pd.Series) -> float:
    has_digit = values.str.contains(r"\d", regex=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    return float((parsed.notna() & has_digit).mean())


def infer_type(s: pd.Series) -> str:
    """What the column holds, judged from its values rather than its storage type.

    "numeric_as_text" means at least 90% of the values are numbers but some are not,
    which is why the column was read as text: usually a data-entry problem.
    """
    values = s.dropna()
    if values.empty:
        return "empty"
    if values.nunique() == 1:
        return "constant"
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    if pd.api.types.is_numeric_dtype(s):
        sequential = values.is_monotonic_increasing or values.is_monotonic_decreasing
        if pd.api.types.is_integer_dtype(s) and values.is_unique and sequential:
            return "identifier"
        return "numeric"

    text = values.astype(str).str.strip()
    text = text[~text.str.lower().isin(DISGUISED_MISSING)]
    if text.empty:
        return "empty"
    sample = text.sample(min(len(text), TYPE_SAMPLE_SIZE), random_state=0)
    if pd.to_numeric(sample, errors="coerce").notna().mean() >= TYPE_MATCH_SHARE:
        return "numeric_as_text"
    if _datetime_share(sample) >= TYPE_MATCH_SHARE:
        return "datetime"
    if text.is_unique:
        return "unique_text"
    if text.nunique() <= max(20, 0.05 * len(text)):
        return "categorical"
    return "text"


# ---------------------------------------------------------------- the checks


def profile_dataset(ds: Dataset, offset: int = 0, limit: int = 50) -> dict:
    frame = ds.frame
    rows, width = frame.shape
    summaries = []
    disguised_total = 0
    for name in frame.columns:
        s = frame[name]
        missing = int(s.isna().sum())
        disguised = int(disguised_missing_mask(s).sum())
        disguised_total += disguised
        distinct = int(s.nunique())
        summaries.append({
            "column": str(name),
            "type": infer_type(s),
            "missing": missing,
            "disguised_missing": disguised,
            "missing_pct": pct(missing + disguised, rows),
            "distinct": distinct,
            "personal_data": pii.personal_kinds(s, str(name)),
        })
    shown, page_info = page(summaries, offset, limit)
    missing_cells = int(frame.isna().sum().sum())
    return {
        "dataset": ds.name,
        "rows": rows,
        "columns": width,
        "missing_cells": missing_cells,
        "disguised_missing_cells": disguised_total,
        "missing_cells_pct": pct(missing_cells + disguised_total, rows * width),
        "duplicate_rows": int(frame.duplicated().sum()),
        "column_profiles": shown,
        "page": page_info,
    }


def describe_column(ds: Dataset, column: str, top: int = 10) -> dict:
    s = get_column(ds, column)
    kind = infer_type(s)
    personal = pii.personal_kinds(s, column)
    disguised = disguised_missing_mask(s)
    present = s[s.notna() & ~disguised]
    result: dict[str, Any] = {
        "dataset": ds.name,
        "column": column,
        "type": kind,
        "rows": len(s),
        "missing": int(s.isna().sum()),
        "disguised_missing": int(disguised.sum()),
        "distinct": int(present.nunique()),
    }

    if kind in {"numeric", "identifier", "numeric_as_text"}:
        numbers = pd.to_numeric(present, errors="coerce")
        if kind == "numeric_as_text":
            bad = present[numbers.isna()]
            result["not_numeric"] = int(len(bad))
            if not personal:
                result["not_numeric_examples"] = [to_json(v) for v in bad.unique()[:5]]
        numbers = numbers.dropna()
        if not numbers.empty:
            result["stats"] = {
                "min": to_json(numbers.min()),
                "p25": to_json(numbers.quantile(0.25)),
                "median": to_json(numbers.median()),
                "p75": to_json(numbers.quantile(0.75)),
                "max": to_json(numbers.max()),
                "mean": to_json(numbers.mean()),
                "std": to_json(numbers.std()),
                "zeros": int((numbers == 0).sum()),
                "negatives": int((numbers < 0).sum()),
            }

    if kind == "datetime" and is_text(s):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dates = pd.to_datetime(present.astype(str), errors="coerce", format="mixed")
        result["dates"] = {
            "earliest": to_json(dates.min()),
            "latest": to_json(dates.max()),
            "unparseable": int(dates.isna().sum()),
        }

    if is_text(s) and not present.empty:
        as_text = present.astype(str)
        lengths = as_text.str.len()
        result["length"] = {
            "min": int(lengths.min()),
            "max": int(lengths.max()),
            "mean": to_json(lengths.mean()),
        }
        result["leading_or_trailing_spaces"] = int((as_text != as_text.str.strip()).sum())
        result["case_or_spacing_variants"] = int(
            as_text.nunique() - normalize_text(as_text).nunique()
        )

    if personal:
        result["values_hidden"] = (
            f"This column looks like it holds personal data ({', '.join(personal)}), "
            "so no values are shown."
        )
    elif kind in {"unique_text", "identifier"}:
        result["top_values_note"] = "Every value appears once, so no common values are listed."
    else:
        counts = present.value_counts().head(top)
        result["top_values"] = [
            {"value": to_json(v), "count": int(n), "pct": pct(int(n), len(s))}
            for v, n in counts.items()
        ]
    return result


def check_missing(ds: Dataset, offset: int = 0, limit: int = 50) -> dict:
    frame = ds.frame
    rows = len(frame)
    masks = {}
    entries = []
    for name in frame.columns:
        s = frame[name]
        disguised = disguised_missing_mask(s)
        masks[name] = s.isna() | disguised
        missing, hidden = int(s.isna().sum()), int(disguised.sum())
        if not (missing or hidden):
            continue
        entry: dict[str, Any] = {
            "column": str(name),
            "missing": missing,
            "disguised_missing": hidden,
            "missing_pct": pct(missing + hidden, rows),
        }
        if hidden:
            tokens = s[disguised].astype(str).str.strip().value_counts().head(5)
            entry["disguised_as"] = {(t or "(blank)"): int(n) for t, n in tokens.items()}
        entries.append(entry)

    entries.sort(key=lambda e: -(e["missing"] + e["disguised_missing"]))
    shown, page_info = page(entries, offset, limit)
    combined = pd.DataFrame(masks)
    return {
        "dataset": ds.name,
        "rows": rows,
        "columns_with_missing": len(entries),
        "rows_with_any_missing": int(combined.any(axis=1).sum()) if rows else 0,
        "fully_empty_rows": int(combined.all(axis=1).sum()) if rows else 0,
        "entirely_missing_columns": [e["column"] for e in entries if e["missing_pct"] == 100.0],
        "missing_by_column": shown,
        "page": page_info,
    }


def find_duplicates(ds: Dataset, columns: list[str] | None = None, limit: int = 10) -> dict:
    frame = ds.frame
    if columns:
        for name in columns:
            get_column(ds, name)
        key = frame[list(columns)]
    else:
        key = frame

    hashes = pd.util.hash_pandas_object(key, index=False).to_numpy()
    by_hash = pd.Series(np.arange(len(key))).groupby(hashes).agg(list)
    groups = sorted((g for g in by_hash if len(g) > 1), key=lambda g: (-len(g), g[0]))
    shown = [{"row_numbers": [int(i) + 1 for i in g], "copies": len(g)} for g in groups[:limit]]

    exact_extra = int(key.duplicated().sum())
    normalized = key.apply(normalize_text)
    near_extra = int(normalized.duplicated().sum()) - exact_extra

    return {
        "dataset": ds.name,
        "compared_columns": list(columns) if columns else "all",
        "rows": len(frame),
        "duplicate_rows": exact_extra,
        "duplicate_groups": len(groups),
        "groups": shown,
        "groups_not_shown": max(0, len(groups) - limit),
        "near_duplicate_rows": near_extra,
        "notes": [
            "duplicate_rows counts the extra copies, so a value that appears 3 times adds 2.",
            "near_duplicate_rows counts further rows that match only once case and spacing "
            "are ignored.",
            ROW_NUMBER_NOTE,
            "Values are not shown. Use describe_column to look inside a column.",
        ],
    }


def detect_pii(ds: Dataset, columns: list[str] | None = None,
               offset: int = 0, limit: int = 50) -> dict:
    names = columns or [str(c) for c in ds.frame.columns]
    findings = []
    for name in names:
        found = pii.scan_column(get_column(ds, name), name)
        if found:
            findings.append({"column": name, "personal_data": found})
    shown, page_info = page(findings, offset, limit)
    return {
        "dataset": ds.name,
        "columns_scanned": len(names),
        "columns_with_personal_data": len(findings),
        "findings": shown,
        "page": page_info,
        "notes": [
            "Values are never returned, and the other tools hide values in these columns.",
            "basis 'values' means the values match the format (with checksums for card numbers "
            "and IBANs); 'column_name' means only the column's name suggests it.",
            f"Large columns are judged on a sample of {pii.MAX_SCAN_VALUES} values.",
            "Pattern matching is a strong hint, not proof: unusual formats can be missed.",
        ],
    }


NUMERIC_TYPES = {"numeric", "numeric_as_text"}


def find_outliers(ds: Dataset, column: str | None = None, method: str = "iqr",
                  limit: int = 10, offset: int = 0) -> dict:
    """Values far outside a column's usual range.

    iqr: outside 1.5 interquartile ranges beyond the middle half of the values (robust).
    zscore: more than 3 standard deviations from the mean (assumes a bell-shaped spread).
    """
    frame = ds.frame
    if column:
        s = get_column(ds, column)
        numbers = pd.to_numeric(s, errors="coerce")
        if numbers.notna().sum() < 0.5 * max(1, s.notna().sum()):
            raise DatasetError(
                f"{column!r} is {infer_type(s)}, not a numeric column, so it has no outliers "
                "to find. Use describe_column to look at its values."
            )
        targets = [column]
    else:
        targets = [str(c) for c in frame.columns if infer_type(frame[c]) in NUMERIC_TYPES]

    entries = []
    for name in targets:
        s = frame[name]
        numbers = pd.to_numeric(s, errors="coerce").reset_index(drop=True)
        valid = numbers.dropna()
        if len(valid) < 4:
            entries.append({"column": name, "skipped": "Fewer than 4 numbers to compare."})
            continue
        if method == "zscore":
            mean, std = valid.mean(), valid.std()
            low, high = (mean - 3 * std, mean + 3 * std) if std > 0 else (mean, mean)
        else:
            q1, q3 = valid.quantile(0.25), valid.quantile(0.75)
            spread = q3 - q1
            low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
        outside = valid[(valid < low) | (valid > high)]
        entry: dict[str, Any] = {
            "column": name,
            "outliers": len(outside),
            "outliers_pct": pct(len(outside), len(valid)),
            "below_range": int((outside < low).sum()),
            "above_range": int((outside > high).sum()),
            "usual_range": {"low": to_json(low), "high": to_json(high)},
        }
        if column and len(outside):
            distance = np.maximum(low - outside, outside - high).sort_values(ascending=False)
            hide = bool(pii.personal_kinds(s, name))
            entry["most_extreme"] = [
                {"row_number": int(i) + 1} if hide
                else {"row_number": int(i) + 1, "value": to_json(outside[i])}
                for i in distance.index[:limit]
            ]
        entries.append(entry)

    entries.sort(key=lambda e: -e.get("outliers", -1))
    shown, page_info = page(entries, offset, limit if not column else 1)
    return {
        "dataset": ds.name,
        "method": method,
        "columns_checked": len(targets),
        "columns_with_outliers": sum(1 for e in entries if e.get("outliers")),
        "results": shown,
        "page": page_info,
        "notes": [
            "Outliers are unusual, not necessarily wrong: check them against what the column "
            "means before calling them errors.",
            ROW_NUMBER_NOTE,
        ],
    }
