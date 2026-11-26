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
    with pytest.raises(DatasetError, match="not a .csv or .tsv"):
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
    ds = Registry(roots=[data_dir]).load("t.tsv", name="tiny")
    assert ds.name == "tiny"
    assert list(ds.frame.columns) == ["a", "b"]
