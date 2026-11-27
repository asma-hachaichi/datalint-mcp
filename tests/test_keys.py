import pytest

from datalint_mcp import keys
from datalint_mcp.datasets import DatasetError, Registry


def test_valid_primary_key(shop):
    result = keys.check_keys(shop["customers"], ["id"])
    assert result["is_valid_key"] is True
    assert result["duplicate_rows"] == 0


def test_key_with_gaps_and_repeats(shop):
    result = keys.check_keys(shop["orders"], ["customer_id"])
    assert result["is_valid_key"] is False
    assert result["rows_missing_a_key_value"] == 1  # row 8
    assert result["duplicate_rows"] == 3  # customers 1, 2 and 3 each ordered twice


def test_repeated_id_in_csv(customers):
    result = keys.check_keys(customers)
    [id_column] = result["id_like_columns"]
    assert id_column["column"] == "id"
    assert id_column["is_valid_key"] is False
    assert id_column["example_duplicate_row_numbers"] == [[3, 9, 10]]


def test_candidate_keys_skip_decimals_and_constants(shop, customers):
    assert "id" in keys.check_keys(shop["customers"])["candidate_keys"]
    candidates = keys.check_keys(customers)["candidate_keys"]
    assert "score" not in candidates
    assert "constant" not in candidates


def test_multi_column_key(customers):
    result = keys.check_keys(customers, ["id", "email"])
    assert result["key"] == ["id", "email"]
    assert result["duplicate_rows"] == 2


def test_declared_foreign_key_finds_orphans(registry, shop):
    result = keys.check_references(registry, shop["orders"])
    [rel] = result["relationships"]
    assert rel["basis"] == "declared"
    assert rel["references"] == "customers.id"
    assert rel["orphans"] == 2
    assert rel["orphan_row_numbers"] == [5, 10]
    assert rel["orphan_examples"] == ["7", "9"]  # 7.0 and 9.0 compare equal to 7 and 9
    assert rel["rows_missing_a_value"] == 1
    assert rel["referenced_column_is_unique"] is True


def test_declared_key_with_unloaded_parent_is_skipped_with_a_reason(data_dir, shop_path):
    registry = Registry(roots=[data_dir])
    [orders] = registry.load(shop_path.name, table="orders")
    [rel] = keys.check_references(registry, orders)["relationships"]
    assert "not loaded" in rel["skipped"]


def test_requested_reference(registry, shop):
    result = keys.check_references(registry, shop["orders"], "customer_id", "customers.id")
    assert result["relationships"][0]["basis"] == "requested"
    assert result["relationships"][0]["orphans"] == 2


def test_relationship_guessed_from_column_names(data_dir):
    (data_dir / "members.csv").write_text("id,plan\n1,a\n2,b\n", encoding="utf-8")
    (data_dir / "visits.csv").write_text("visit,member_id\n1,1\n2,3\n", encoding="utf-8")
    registry = Registry(roots=[data_dir])
    registry.load("members.csv")
    [visits] = registry.load("visits.csv")
    [rel] = keys.check_references(registry, visits)["relationships"]
    assert rel["basis"] == "name_pattern"
    assert rel["references"] == "members.id"
    assert rel["orphan_row_numbers"] == [2]


def test_reference_needs_both_arguments(registry, shop):
    with pytest.raises(DatasetError, match="Pass both column and references"):
        keys.check_references(registry, shop["orders"], column="customer_id")


def test_reference_format_is_explained(registry, shop):
    with pytest.raises(DatasetError, match="must look like 'dataset.column'"):
        keys.check_references(registry, shop["orders"], "customer_id", "customers")
