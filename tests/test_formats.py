"""Excel workbooks and Parquet files: loaded like the other formats, audited the same way."""

import pandas as pd
import pytest

from datalint_mcp import checks, datasets, keys
from datalint_mcp.datasets import DatasetError, Registry


# ------------------------------------------------------------ Parquet


def test_parquet_finds_the_same_problems_as_the_csv(registry, customers, messy_parquet_path):
    [parquet] = registry.load(messy_parquet_path.name)
    assert parquet.name == "customers"  # loaded after the CSV, so it replaces it
    as_csv, as_parquet = checks.profile_dataset(customers), checks.profile_dataset(parquet)
    for key in ("rows", "columns", "duplicate_rows", "disguised_missing_cells"):
        assert as_parquet[key] == as_csv[key], key
    [age] = [e for e in checks.check_missing(parquet)["missing_by_column"] if e["column"] == "age"]
    assert age["disguised_as"] == {"N/A": 1, "null": 1}


def test_parquet_keeps_real_types_and_dates(data_dir):
    pd.DataFrame({
        "joined": pd.to_datetime(["2024-01-05", "2024-03-01", None, "2024-02-10"]),
        "active": [True, False, True, True],
        "tier": pd.Categorical(["gold", "N/A", "silver", "gold"]),
    }).to_parquet(data_dir / "members.parquet")
    [ds] = Registry(roots=[data_dir]).load("members.parquet")
    types = {p["column"]: p["type"] for p in checks.profile_dataset(ds)["column_profiles"]}
    assert types == {"joined": "datetime", "active": "boolean", "tier": "categorical"}
    dates = checks.describe_column(ds, "joined")["dates"]
    assert dates["earliest"].startswith("2024-01-05")
    assert dates["latest"].startswith("2024-03-01")
    assert checks.describe_column(ds, "tier")["disguised_missing"] == 1


def test_parquet_row_limit_is_checked_before_loading(data_dir, monkeypatch):
    pd.DataFrame({"x": range(50)}).to_parquet(data_dir / "big.parquet")
    monkeypatch.setattr(datasets, "MAX_PARQUET_ROWS", 10)
    with pytest.raises(DatasetError, match="50 rows, over the limit of 10"):
        Registry(roots=[data_dir]).load("big.parquet")


def test_corrupt_parquet_is_refused(data_dir):
    (data_dir / "broken.parquet").write_bytes(b"not parquet at all")
    with pytest.raises(DatasetError, match="could not be read as a Parquet file"):
        Registry(roots=[data_dir]).load("broken.parquet")


def test_table_is_refused_for_parquet(registry, messy_parquet_path):
    with pytest.raises(DatasetError, match="only applies to SQLite tables and Excel sheets"):
        registry.load(messy_parquet_path.name, table="x")


# ------------------------------------------------------------ Excel


def test_excel_loads_every_sheet(registry, shop_workbook_path):
    loaded = {d.name: d for d in registry.load(shop_workbook_path.name)}
    assert set(loaded) == {"customers", "orders"}
    assert loaded["orders"].frame.shape == (12, 5)
    assert loaded["orders"].table == "orders"


def test_excel_finds_the_same_problems_as_the_database(registry, shop_workbook_path):
    data = {d.name: d for d in registry.load(shop_workbook_path.name)}
    # A workbook declares no foreign keys, so the link is guessed from the column names.
    [rel] = keys.check_references(registry, data["orders"])["relationships"]
    assert rel["basis"] == "name_pattern"
    assert rel["orphan_row_numbers"] == [5, 10]
    outliers = checks.find_outliers(data["orders"], "amount")["results"][0]
    assert outliers["most_extreme"] == [{"row_number": 7, "value": 5000.0}]
    found = {f["column"] for f in checks.detect_pii(data["customers"])["findings"]}
    assert found == {"name", "email", "phone", "card"}


def test_excel_single_sheet_with_custom_name(registry, shop_workbook_path):
    [ds] = registry.load(shop_workbook_path.name, name="buyers", table="customers")
    assert ds.name == "buyers"
    assert ds.table == "customers"


def test_excel_unknown_sheet_lists_the_real_ones(registry, shop_workbook_path):
    with pytest.raises(DatasetError, match="no sheet 'invoices'. Its sheets: customers, orders"):
        registry.load(shop_workbook_path.name, table="invoices")


def test_excel_name_needs_a_single_sheet(registry, shop_workbook_path):
    with pytest.raises(DatasetError, match="name only works when loading one sheet"):
        registry.load(shop_workbook_path.name, name="everything")


def test_excel_keeps_placeholder_text_and_real_dates(data_dir):
    with pd.ExcelWriter(data_dir / "hires.xlsx") as writer:
        pd.DataFrame({
            "employee": [1, 2, 3, 4],
            "start_date": [pd.Timestamp("2024-02-01"), "TBD", pd.Timestamp("2024-05-13"), "TBA"],
        }).to_excel(writer, sheet_name="hires", index=False)
    [ds] = Registry(roots=[data_dir]).load("hires.xlsx")
    [entry] = checks.check_missing(ds)["missing_by_column"]
    assert entry["disguised_as"] == {"TBD": 1, "TBA": 1}
    assert checks.describe_column(ds, "start_date")["type"] == "datetime"


def test_empty_sheet_loads_and_profiles_cleanly(data_dir):
    with pd.ExcelWriter(data_dir / "partly_empty.xlsx") as writer:
        pd.DataFrame({"a": [1, 2]}).to_excel(writer, sheet_name="data", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="notes", index=False)
    loaded = {d.name: d for d in Registry(roots=[data_dir]).load("partly_empty.xlsx")}
    assert checks.profile_dataset(loaded["notes"])["rows"] == 0


def test_old_excel_format_gets_a_clear_refusal(data_dir):
    (data_dir / "legacy.xls").write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(DatasetError, match="save it as .xlsx"):
        Registry(roots=[data_dir]).load("legacy.xls")


def test_corrupt_workbook_is_refused(data_dir):
    (data_dir / "broken.xlsx").write_text("not a workbook", encoding="utf-8")
    with pytest.raises(DatasetError, match="could not be read as an Excel workbook"):
        Registry(roots=[data_dir]).load("broken.xlsx")
