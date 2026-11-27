"""The planted problems are real: the datalint tools find exactly what each scenario claims.

If one of these fails, the eval data is wrong, not the model.
"""

import pytest

from datalint_mcp import checks, keys
from datalint_mcp.datasets import Registry
from evals import scenarios as sc


def load(scenario, tmp_path, file_name):
    scenario.build(tmp_path)
    registry = Registry(roots=[tmp_path])
    return registry, {d.name: d for d in registry.load(file_name)}


def test_duplicate_customers(tmp_path):
    _, data = load(sc.DUPLICATES, tmp_path, "customers.csv")
    result = checks.find_duplicates(data["customers"])
    assert result["duplicate_rows"] == sc.DUPLICATES.truth["exact_duplicate_rows"]
    assert result["near_duplicate_rows"] == sc.DUPLICATES.truth["near_duplicate_rows"]


def test_disguised_missing(tmp_path):
    _, data = load(sc.MISSING, tmp_path, "survey.csv")
    [income] = [e for e in checks.check_missing(data["survey"])["missing_by_column"]
                if e["column"] == "income"]
    assert income["disguised_missing"] == sc.MISSING.truth["income_disguised_missing"]
    assert income["missing"] == sc.MISSING.truth["income_empty"]


def test_orphaned_orders(tmp_path):
    registry, data = load(sc.ORPHANS, tmp_path, "store.sqlite")
    [rel] = keys.check_references(registry, data["orders"])["relationships"]
    assert rel["orphans"] == sc.ORPHANS.truth["orphans"]
    assert rel["orphan_row_numbers"] == sc.ORPHANS.truth["orphan_rows"]


def test_extreme_outlier(tmp_path):
    _, data = load(sc.OUTLIER, tmp_path, "sensors.csv")
    result = checks.find_outliers(data["sensors"], "temperature_c")
    top = result["results"][0]["most_extreme"][0]
    assert top == {"row_number": sc.OUTLIER.truth["outlier_row"],
                   "value": sc.OUTLIER.truth["outlier_value"]}


def test_personal_data(tmp_path):
    _, data = load(sc.PERSONAL, tmp_path, "contacts.csv")
    found = {f["column"] for f in checks.detect_pii(data["contacts"])["findings"]}
    assert found == sc.PERSONAL.truth["personal_columns"]


def test_clean_data_has_nothing_to_report(tmp_path):
    _, data = load(sc.CLEAN, tmp_path, "products.csv")
    products = data["products"]
    assert checks.profile_dataset(products)["duplicate_rows"] == 0
    assert checks.check_missing(products)["columns_with_missing"] == 0
    assert checks.find_outliers(products)["columns_with_outliers"] == 0
    assert checks.detect_pii(products)["columns_with_personal_data"] == 0
    assert keys.check_keys(products, ["product_id"])["is_valid_key"] is True


def test_bad_key(tmp_path):
    _, data = load(sc.BAD_KEY, tmp_path, "order_lines.csv")
    result = keys.check_keys(data["order_lines"], ["line_id"])
    assert result["duplicate_rows"] == sc.BAD_KEY.truth["duplicate_key_rows"]
    assert result["example_duplicate_row_numbers"] == sc.BAD_KEY.truth["groups"]


def test_excel_workbook(tmp_path):
    registry, data = load(sc.WORKBOOK, tmp_path, "staff.xlsx")
    # No declared foreign keys in a workbook: the link must be guessed from the column names.
    [rel] = keys.check_references(registry, data["employees"])["relationships"]
    assert rel["references"] == "departments.id"
    assert rel["orphan_row_numbers"] == sc.WORKBOOK.truth["orphan_rows"]
    [start] = [e for e in checks.check_missing(data["employees"])["missing_by_column"]
               if e["column"] == "start_date"]
    assert start["disguised_missing"] == sc.WORKBOOK.truth["start_date_placeholders"]


@pytest.mark.parametrize("scenario", [s for s in sc.SCENARIOS if s.secrets], ids=lambda s: s.id)
def test_secrets_are_really_in_the_data(scenario, tmp_path):
    """A leak check is only meaningful if the values it looks for exist."""
    scenario.build(tmp_path)
    content = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir()).lower()
    for secret in scenario.secrets:
        assert secret.lower() in content


def test_scenarios_are_deterministic(tmp_path):
    for scenario in sc.SCENARIOS:
        first, second = tmp_path / f"{scenario.id}-1", tmp_path / f"{scenario.id}-2"
        first.mkdir()
        second.mkdir()
        scenario.build(first)
        scenario.build(second)
        for path in first.iterdir():
            if path.suffix == ".xlsx":
                # Workbooks embed their save time, so compare what's in them, not the bytes.
                import pandas as pd

                one = pd.read_excel(path, sheet_name=None)
                two = pd.read_excel(second / path.name, sheet_name=None)
                assert one.keys() == two.keys(), scenario.id
                for sheet in one:
                    pd.testing.assert_frame_equal(one[sheet], two[sheet])
            else:
                assert path.read_bytes() == (second / path.name).read_bytes(), scenario.id
