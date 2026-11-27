import importlib.util
from pathlib import Path

import pytest

from datalint_mcp.datasets import Registry

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def pytest_addoption(parser):
    parser.addoption("--release-tag", default=None,
                     help="The git tag being released, checked against the code's version.")


def _load_example_module():
    spec = importlib.util.spec_from_file_location("make_shop_db", EXAMPLES / "make_shop_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_example_builder():
    return _load_example_module().build

# Ten rows with known problems:
# - rows 3, 9 and 10 are identical
# - "ANA@example.com " is ana@example.com with different case and a trailing space
# - age uses "N/A" and "null" as placeholders
# - country has "france" and " Italy" alongside "France" and "Italy"
# - score has one empty cell, one zero and one negative
# - "constant" holds the same value everywhere, "empty" holds nothing
MESSY_CSV = """\
id,email,age,signup_date,country,score,constant,empty
1,ana@example.com,34,2024-01-05,France,88.5,x,
2,ben@example.com,N/A,2024-02-11,France,72,x,
3,cara@example.com,29,2024-03-20,Spain,91,x,
4,dan@example.com,41,2024-04-02,Spain,-5,x,
5,ANA@example.com ,34,2024-05-09,france,60,x,
6,eve@example.com,null,2024-06-15,Italy,0,x,
7,fay@example.com,52,2024-07-01, Italy,77,x,
8,gus@example.com,38,2024-08-19,Spain,,x,
3,cara@example.com,29,2024-03-20,Spain,91,x,
3,cara@example.com,29,2024-03-20,Spain,91,x,
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "customers.csv").write_text(MESSY_CSV, encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(data_dir: Path) -> Registry:
    return Registry(roots=[data_dir])


@pytest.fixture
def customers(registry: Registry):
    return registry.load("customers.csv")[0]


@pytest.fixture
def shop_path(data_dir: Path) -> Path:
    """The example database (see examples/make_shop_db.py for its planted problems)."""
    return _load_example_builder()(data_dir / "shop.sqlite")


@pytest.fixture
def shop(registry: Registry, shop_path: Path) -> dict:
    return {d.name: d for d in registry.load(shop_path.name)}


@pytest.fixture
def shop_workbook_path(data_dir: Path) -> Path:
    """The same shop tables as an Excel workbook, one sheet each."""
    return _load_example_module().build_workbook(data_dir / "shop.xlsx")


@pytest.fixture
def messy_parquet_path(data_dir: Path) -> Path:
    """customers.csv saved as Parquet, placeholders and all."""
    import pandas as pd

    frame = pd.read_csv(data_dir / "customers.csv", keep_default_na=False, na_values=[""])
    path = data_dir / "customers.parquet"
    frame.to_parquet(path)
    return path


@pytest.fixture
def anyio_backend():
    return "asyncio"
