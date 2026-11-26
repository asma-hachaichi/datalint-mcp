from pathlib import Path

import pandas as pd
import pytest

from datalint_mcp import checks
from datalint_mcp.datasets import Dataset, DatasetError


def frame_dataset(**columns) -> Dataset:
    return Dataset(name="t", path=Path("t.csv"), frame=pd.DataFrame(columns))


# ------------------------------------------------------------ types


def test_column_types_on_messy_data(customers):
    types = {
        p["column"]: p["type"]
        for p in checks.profile_dataset(customers)["column_profiles"]
    }
    assert types["age"] == "numeric_as_text"
    assert types["signup_date"] == "datetime"
    assert types["country"] == "categorical"
    assert types["score"] == "numeric"
    assert types["constant"] == "constant"
    assert types["empty"] == "empty"


def test_sequential_unique_integers_are_identifiers():
    ds = frame_dataset(id=list(range(1, 31)))
    assert checks.infer_type(ds.frame["id"]) == "identifier"


def test_unique_free_text_is_unique_text():
    ds = frame_dataset(comment=[f"comment number {i}" for i in range(30)])
    assert checks.infer_type(ds.frame["comment"]) == "unique_text"


# ------------------------------------------------------------ profile


def test_profile_totals(customers):
    result = checks.profile_dataset(customers)
    assert result["rows"] == 10
    assert result["columns"] == 8
    assert result["missing_cells"] == 11  # 10 in "empty" + 1 in "score"
    assert result["disguised_missing_cells"] == 2  # "N/A" and "null" in "age"
    assert result["duplicate_rows"] == 2


def test_profile_pages_columns(customers):
    first = checks.profile_dataset(customers, offset=0, limit=3)
    assert [p["column"] for p in first["column_profiles"]] == ["id", "email", "age"]
    assert first["page"] == {"offset": 0, "limit": 3, "total": 8, "next_offset": 3}
    last = checks.profile_dataset(customers, offset=6, limit=3)
    assert last["page"]["next_offset"] is None
    assert len(last["column_profiles"]) == 2


# ------------------------------------------------------------ describe_column


def test_describe_numeric_column(customers):
    result = checks.describe_column(customers, "score")
    assert result["missing"] == 1
    assert result["stats"]["min"] == -5
    assert result["stats"]["max"] == 91
    assert result["stats"]["zeros"] == 1
    assert result["stats"]["negatives"] == 1


def test_describe_text_column_finds_spacing_and_case(customers):
    result = checks.describe_column(customers, "country")
    assert result["leading_or_trailing_spaces"] == 1  # " Italy"
    assert result["case_or_spacing_variants"] == 2  # "france" and " Italy"
    assert result["top_values"][0] == {"value": "Spain", "count": 5, "pct": 50.0}


def test_describe_numbers_stored_as_text_lists_the_odd_ones_out():
    ds = frame_dataset(price=[str(i) for i in range(19)] + ["twelve"])
    result = checks.describe_column(ds, "price")
    assert result["type"] == "numeric_as_text"
    assert result["not_numeric"] == 1
    assert result["not_numeric_examples"] == ["twelve"]
    assert result["stats"]["max"] == 18


def test_describe_dates(customers):
    result = checks.describe_column(customers, "signup_date")
    assert result["dates"]["earliest"].startswith("2024-01-05")
    assert result["dates"]["latest"].startswith("2024-08-19")
    assert result["dates"]["unparseable"] == 0


def test_describe_skips_common_values_when_all_unique():
    ds = frame_dataset(comment=[f"comment number {i}" for i in range(30)])
    result = checks.describe_column(ds, "comment")
    assert "top_values" not in result
    assert "top_values_note" in result


def test_long_values_are_clipped():
    ds = frame_dataset(text=["a" * 500] * 3 + ["b"])
    value = checks.describe_column(ds, "text")["top_values"][0]["value"]
    assert len(value) == checks.MAX_TEXT_CHARS + 1
    assert value.endswith("…")


def test_unknown_column_suggests_close_names(customers):
    with pytest.raises(DatasetError, match="Did you mean 'country'"):
        checks.describe_column(customers, "contry")


# ------------------------------------------------------------ check_missing


def test_check_missing(customers):
    result = checks.check_missing(customers)
    by_column = {e["column"]: e for e in result["missing_by_column"]}
    assert [e["column"] for e in result["missing_by_column"]] == ["empty", "age", "score"]
    assert by_column["age"]["disguised_as"] == {"N/A": 1, "null": 1}
    assert by_column["score"]["missing"] == 1
    assert result["entirely_missing_columns"] == ["empty"]
    assert result["rows_with_any_missing"] == 10
    assert result["fully_empty_rows"] == 0


def test_blank_spaces_count_as_disguised_missing():
    ds = frame_dataset(city=["Paris", "   ", "Lyon", "-"])
    entry = checks.check_missing(ds)["missing_by_column"][0]
    assert entry["disguised_missing"] == 2
    assert entry["disguised_as"] == {"(blank)": 1, "-": 1}


# ------------------------------------------------------------ find_duplicates


def test_whole_row_duplicates(customers):
    result = checks.find_duplicates(customers)
    assert result["duplicate_rows"] == 2
    assert result["duplicate_groups"] == 1
    assert result["groups"] == [{"row_numbers": [3, 9, 10], "copies": 3}]
    assert result["near_duplicate_rows"] == 0


def test_key_column_duplicates_and_near_duplicates(customers):
    result = checks.find_duplicates(customers, columns=["email"])
    assert result["duplicate_rows"] == 2  # cara appears 3 times
    assert result["near_duplicate_rows"] == 1  # "ANA@example.com " matches ana
    assert "ana@example.com" not in str(result)  # values are never echoed


def test_duplicate_groups_respect_limit():
    ds = frame_dataset(k=["a", "a", "b", "b", "c", "c"])
    result = checks.find_duplicates(ds, limit=2)
    assert len(result["groups"]) == 2
    assert result["groups_not_shown"] == 1


def test_duplicates_on_unknown_column(customers):
    with pytest.raises(DatasetError, match="has no column"):
        checks.find_duplicates(customers, columns=["mail"])
