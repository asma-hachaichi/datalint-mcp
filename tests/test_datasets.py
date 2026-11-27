import pytest

from datalint_mcp.datasets import DatasetError, Registry


def test_loads_csv_and_keeps_placeholder_text(customers):
    assert customers.name == "customers"
    assert customers.frame.shape == (10, 8)
    # "N/A" stays as written instead of being turned into a missing value
    assert "N/A" in customers.frame["age"].tolist()


def test_refuses_files_outside_allowed_folders(registry, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="outside the folders"):
        registry.load(str(outside))


def test_refuses_path_escaping_with_dot_dot(registry):
    with pytest.raises(DatasetError, match="outside the folders"):
        registry.load("../customers.csv")


def test_refuses_unsupported_format(registry, data_dir):
    (data_dir / "notes.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(DatasetError, match="not a supported file"):
        registry.load("notes.txt")


def test_refuses_missing_file(registry):
    with pytest.raises(DatasetError, match="no file"):
        registry.load("nope.csv")


def test_refuses_empty_file(registry, data_dir):
    (data_dir / "blank.csv").write_text("", encoding="utf-8")
    with pytest.raises(DatasetError, match="empty"):
        registry.load("blank.csv")


def test_unknown_dataset_names_what_is_loaded(registry, customers):
    with pytest.raises(DatasetError, match="Loaded: customers"):
        registry.get("orders")


def test_custom_name_and_tsv(data_dir):
    (data_dir / "t.tsv").write_text("a\tb\n1\t2\n", encoding="utf-8")
    [ds] = Registry(roots=[data_dir]).load("t.tsv", name="tiny")
    assert ds.name == "tiny"
    assert list(ds.frame.columns) == ["a", "b"]


# ------------------------------------------------------------ SQLite


def test_sqlite_loads_every_table_with_declared_foreign_keys(registry, shop_path):
    loaded = {d.name: d for d in registry.load(shop_path.name)}
    assert set(loaded) == {"customers", "orders"}
    assert loaded["orders"].frame.shape == (12, 5)
    [fk] = loaded["orders"].foreign_keys
    assert (fk.column, fk.ref_table, fk.ref_column) == ("customer_id", "customers", "id")


def test_sqlite_single_table_with_custom_name(registry, shop_path):
    [ds] = registry.load(shop_path.name, name="buyers", table="customers")
    assert ds.name == "buyers"
    assert ds.table == "customers"


def test_sqlite_unknown_table_lists_the_real_ones(registry, shop_path):
    with pytest.raises(DatasetError, match="Its tables: customers, orders"):
        registry.load(shop_path.name, table="invoices")


def test_sqlite_name_needs_a_single_table(registry, shop_path):
    with pytest.raises(DatasetError, match="name only works when loading one table"):
        registry.load(shop_path.name, name="everything")


def test_table_is_refused_for_csv(registry):
    with pytest.raises(DatasetError, match="only applies to SQLite"):
        registry.load("customers.csv", table="x")


def test_fake_database_file_is_refused(registry, data_dir):
    (data_dir / "fake.db").write_text("not a database at all", encoding="utf-8")
    with pytest.raises(DatasetError, match="could not be read as a SQLite database"):
        registry.load("fake.db")


def test_sqlite_is_opened_read_only(registry, shop_path):
    before = shop_path.read_bytes()
    registry.load(shop_path.name)
    assert shop_path.read_bytes() == before
