"""Build examples/shop.sqlite and examples/shop.xlsx: two related tables with planted problems.

The workbook holds the same two tables as sheets. It declares no foreign keys, so datalint
finds the customers link from the column names instead.

Planted problems:
- orders.customer_id points at customers 7 and 9, who do not exist (rows 5 and 10)
- orders row 8 has no customer
- orders row 7 has an amount of 5000, far above the usual 25 to 52
- customers hold names, emails, phone numbers and card numbers (test card numbers)
- orders.ip holds IP addresses, and one note mentions an email address

Run: python examples/make_shop_db.py
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

CUSTOMERS = [
    (1, "Ana Lopez", "ana@example.com", "+33 6 12 34 56 78", "4111 1111 1111 1111", "Paris"),
    (2, "Ben Carter", "ben@example.com", "+44 20 7946 0958", "5500 0000 0000 0004", "London"),
    (3, "Cara Smith", "cara@example.com", "+1 (415) 555-0132", "4012 8888 8888 1881", "Austin"),
    (4, "Dan Brown", "dan@example.com", "+33 1 23 45 67 89", "3782 822463 10005", "Lyon"),
    (5, "Eve Martin", "eve@example.com", "06 98 76 54 32", "6011 1111 1111 1117", "Nice"),
    (6, "Fay Wong", "fay@example.com", "+852 2123 4567", "3530 1113 3330 0000", "Hong Kong"),
]

ORDERS = [
    (1, 1, 25.0, "192.168.1.10", None),
    (2, 2, 40.0, "10.0.0.5", None),
    (3, 3, 35.5, "172.16.0.1", "gift wrap"),
    (4, 1, 52.0, "192.168.1.11", None),
    (5, 7, 30.0, "10.0.0.8", None),
    (6, 4, 45.0, "192.168.1.12", "call me at bob@example.com"),
    (7, 5, 5000.0, "10.0.0.9", None),
    (8, None, 28.0, "192.168.1.13", None),
    (9, 6, 33.0, "10.0.0.10", None),
    (10, 9, 41.0, "172.16.0.2", None),
    (11, 2, 38.0, "192.168.1.14", None),
    (12, 3, 47.0, "10.0.0.11", None),
]


def build(path: Path) -> Path:
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as con, con:
        con.execute(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
            "phone TEXT, card TEXT, city TEXT)"
        )
        con.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
            "customer_id INTEGER REFERENCES customers(id), amount REAL, ip TEXT, notes TEXT)"
        )
        con.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", CUSTOMERS)
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
    return path


def build_workbook(path: Path) -> Path:
    import pandas as pd

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(CUSTOMERS, columns=["id", "name", "email", "phone", "card", "city"]) \
            .to_excel(writer, sheet_name="customers", index=False)
        pd.DataFrame(ORDERS, columns=["id", "customer_id", "amount", "ip", "notes"]) \
            .to_excel(writer, sheet_name="orders", index=False)
    return path


if __name__ == "__main__":
    here = Path(__file__).parent
    print(build(here / "shop.sqlite"))
    print(build_workbook(here / "shop.xlsx"))
