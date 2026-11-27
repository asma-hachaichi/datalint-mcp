"""Eval scenarios: datasets with planted problems, and what a good audit report must say.

Each scenario builds its data deterministically, so the same problems sit at the same rows on
every run. `truth` records the planted problems; tests/test_eval_scenarios.py checks that the
datalint tools themselves find exactly those, so a failed eval always means the model (or the
tool descriptions and skill guiding it) fell short, never broken test data.
"""

from __future__ import annotations

import csv
import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 9: "nine", 12: "twelve"}


def number(n: int) -> str:
    """A regex matching n as digits or as a word."""
    word = NUMBER_WORDS.get(n)
    return rf"\b(?:{n}{'|' + word if word else ''})\b"


def near(a: str, b: str, gap: int = 80) -> str:
    """A regex matching a and b within `gap` characters of each other, in either order."""
    return rf"(?:{a})[^\n]{{0,{gap}}}(?:{b})|(?:{b})[^\n]{{0,{gap}}}(?:{a})"


@dataclass
class Check:
    name: str
    pattern: str  # searched in the final report, case-insensitive


@dataclass
class Scenario:
    id: str
    title: str
    prompt: str
    build: Callable[[Path], None]
    expect: list[Check]
    forbid: list[Check] = field(default_factory=list)
    # Each set is one requirement: at least one tool in it must be called.
    required_tools: list[set[str]] = field(default_factory=list)
    # Values that must never appear anywhere in the transcript: tool inputs, tool results
    # or the report. Planted personal data, to check the server's guardrails end to end.
    secrets: list[str] = field(default_factory=list)
    max_tool_calls: int = 12
    truth: dict = field(default_factory=dict)


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


# ---------------------------------------------------------------- duplicate customers

def _customer_rows() -> list[list]:
    countries = ["France", "Spain", "Italy", "Germany"]
    rows = [[i, f"Customer {i}", f"user{i}@example.com", countries[i % 4], f"2025-{i % 12 + 1:02d}-15"]
            for i in range(1, 41)]
    rows += [list(rows[4]), list(rows[11]), list(rows[29])]  # rows 41-43: exact copies
    for source in (rows[7], rows[19]):  # rows 44-45: same rows with case and spacing changed
        copy = list(source)
        copy[1] = copy[1].lower() + " "
        copy[2] = copy[2].upper()
        rows.append(copy)
    return rows


def _build_duplicates(folder: Path) -> None:
    _write_csv(folder / "customers.csv", ["id", "customer", "email", "country", "signup_date"],
               _customer_rows())


DUPLICATES = Scenario(
    id="duplicate_customers",
    title="Exact and near-duplicate customer rows",
    prompt="Audit customers.csv for duplicates before I import it into our CRM.",
    build=_build_duplicates,
    expect=[
        Check("counts the 3 exact duplicate rows",
              near(number(3), r"duplicat|identical|exact cop|cop(?:y|ies|ied)|twice|"
                              r"word for word|repeated")),
        Check("mentions rows that differ only by case or spacing",
              r"case|spacing|whitespace|capitali[sz]|near[- ]?duplicate|formatting"),
    ],
    required_tools=[{"find_duplicates"}],
    secrets=["user5@example.com", "user12@example.com", "USER8@EXAMPLE.COM"],
    truth={"exact_duplicate_rows": 3, "near_duplicate_rows": 2},
)


# ---------------------------------------------------------------- disguised missing values

def _survey_rows() -> list[list]:
    rng = random.Random(2)
    placeholders = {5: "N/A", 11: "N/A", 23: "N/A", 40: "N/A", 8: "-", 17: "-", 33: "-",
                    27: "unknown", 46: "unknown", 14: "", 30: "", 38: ""}
    rows = []
    for i in range(1, 51):
        income = placeholders.get(i, rng.randrange(28_000, 95_000, 500))
        rows.append([i, rng.randint(19, 70), income, rng.choice(["Paris", "Lyon", "Lille"])])
    return rows


def _build_missing(folder: Path) -> None:
    _write_csv(folder / "survey.csv", ["respondent_id", "age", "income", "city"], _survey_rows())


MISSING = Scenario(
    id="disguised_missing",
    title="Missing incomes hidden behind placeholder text",
    prompt="I'm about to compute average income from survey.csv. Is the data complete enough?",
    build=_build_missing,
    expect=[
        Check("names the income column", r"\bincome\b"),
        Check("names the N/A placeholder", r"N/A"),
        Check("quantifies the gap", r"\b(?:12|twelve|9|nine)\b|24(?:\.0+)?\s?%"),
    ],
    required_tools=[{"check_missing"}],
    truth={"income_disguised_missing": 9, "income_empty": 3},
)


# ---------------------------------------------------------------- orphaned orders

ORPHAN_ROWS = {7: 101, 19: 102, 33: 103, 48: 104}


def _build_orphans(folder: Path) -> None:
    rng = random.Random(3)
    path = folder / "store.sqlite"
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as con, con:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, segment TEXT)")
        con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, "
                    "customer_id INTEGER REFERENCES customers(id), amount REAL)")
        con.executemany("INSERT INTO customers VALUES (?, ?)",
                        [(i, rng.choice(["retail", "pro"])) for i in range(1, 26)])
        con.executemany("INSERT INTO orders VALUES (?, ?, ?)", [
            (i, ORPHAN_ROWS.get(i, rng.randint(1, 25)), round(rng.uniform(20, 80), 2))
            for i in range(1, 61)
        ])


ORPHANS = Scenario(
    id="orphaned_orders",
    title="Orders pointing at customers that don't exist",
    prompt="Check store.sqlite. Can I trust revenue-per-customer numbers built on it?",
    build=_build_orphans,
    expect=[
        Check("counts the 4 orphaned orders",
              near(number(4), r"orphan|n[o']?t exist|non-?existent|missing customer|"
                              r"unknown customer|no matching|invalid customer")),
        Check("points at an orphaned row", r"\b(?:19|33|48)\b"),
    ],
    required_tools=[{"check_references"}],
    truth={"orphans": 4, "orphan_rows": sorted(ORPHAN_ROWS)},
)


# ---------------------------------------------------------------- extreme outlier

def _sensor_rows() -> list[list]:
    rng = random.Random(4)
    rows = [[i, "ABC"[i % 3], round(20 + rng.gauss(0, 1.5), 1)] for i in range(1, 61)]
    rows[36][2] = 999.0  # row 37
    return rows


def _build_outlier(folder: Path) -> None:
    _write_csv(folder / "sensors.csv", ["reading_id", "sensor", "temperature_c"], _sensor_rows())


OUTLIER = Scenario(
    id="extreme_outlier",
    title="One impossible temperature reading",
    prompt="sensors.csv has room temperature readings. Anything off before I chart it?",
    build=_build_outlier,
    expect=[
        Check("reports the 999 reading", r"\b999\b"),
        Check("gives its row", r"\b37\b"),
    ],
    required_tools=[{"find_outliers"}],
    truth={"outlier_row": 37, "outlier_value": 999.0},
)


# ---------------------------------------------------------------- personal data

def _contact_rows() -> list[list]:
    first = ["Ana", "Ben", "Cara", "Dan", "Eve", "Fay", "Gus", "Hana", "Ivo", "Jun"]
    last = ["Lopez", "Carter", "Smith", "Brown", "Martin"]
    rows = []
    for i in range(1, 21):
        name = f"{first[i % 10]} {last[i % 5]}"
        rows.append([i, name, f"{name.split()[0].lower()}.{i}@mail.example",
                     f"+33 6 12 34 {i:02d} {(i * 7) % 100:02d}", f"Company {i % 4}",
                     "met at conference" if i % 3 == 0 else ""])
    return rows


def _build_contacts(folder: Path) -> None:
    _write_csv(folder / "contacts.csv",
               ["contact_id", "full_name", "email", "phone", "company", "notes"], _contact_rows())


PERSONAL = Scenario(
    id="personal_data",
    title="Contact list full of personal data",
    prompt="I want to send contacts.csv to an outside agency. Audit it first.",
    build=_build_contacts,
    expect=[
        Check("flags personal data", r"personal|PII|sensitive|private"),
        Check("names the email column", r"\bemail"),
        Check("names the phone column", r"\bphone"),
        Check("names the name column", r"full_name|\bnames?\b"),
    ],
    required_tools=[{"detect_pii", "profile_dataset"}],
    secrets=[row[2] for row in _contact_rows()[:5]] + [row[3] for row in _contact_rows()[:5]],
    truth={"personal_columns": {"full_name", "email", "phone"}},
)


# ---------------------------------------------------------------- clean data

def _product_rows() -> list[list]:
    rng = random.Random(6)
    return [[i, f"Product {i}", rng.choice(["tools", "garden", "kitchen", "toys"]),
             round(rng.uniform(5, 100), 2), rng.randint(0, 200)] for i in range(1, 41)]


def _build_clean(folder: Path) -> None:
    _write_csv(folder / "products.csv", ["product_id", "product_name", "category", "price", "stock"],
               _product_rows())


CLEAN = Scenario(
    id="clean_data",
    title="A clean dataset: no problems to invent",
    prompt="Quick check: is products.csv ready to use?",
    build=_build_clean,
    expect=[
        # "ready" and "clean" only count when not negated: "not ready" is the opposite verdict.
        Check("gives a clean verdict",
              r"(?<!not )(?<!n't )(?<!isn't )\b(?:ready|clean)\b|good shape|"
              r"no (?:major |significant |serious |real )?(?:issues|problems|quality issues)"),
    ],
    forbid=[
        Check("invents duplicate rows", r"\b[1-9]\d*\s+(?:exact\s+)?duplicate(?:d)?\s+rows?"),
        Check("invents outliers", r"\b[1-9]\d*\s+outliers?\b"),
        Check("invents orphans", r"\b[1-9]\d*\s+orphan"),
    ],
    max_tool_calls=8,
    truth={"problems": 0},
)


# ---------------------------------------------------------------- broken key

def _line_rows() -> list[list]:
    rng = random.Random(7)
    rows = [[i, rng.randint(1000, 1020), rng.choice(["pen", "ink", "pad"]), rng.randint(1, 5)]
            for i in range(1, 51)]
    rows[30][0] = 17  # row 31 reuses id 17
    rows[43][0] = 22  # row 44 reuses id 22
    return rows


def _build_key(folder: Path) -> None:
    _write_csv(folder / "order_lines.csv", ["line_id", "order_id", "item", "quantity"], _line_rows())


BAD_KEY = Scenario(
    id="bad_key",
    title="A primary key that isn't unique",
    prompt="Can I use line_id as the primary key of order_lines.csv?",
    build=_build_key,
    expect=[
        Check("answers no", r"\bno\b|\bnot\b|\bcan(?:'|no)t\b"),
        Check("says line_id repeats",
              near(r"line_id", r"duplicat|repeat|not unique|reused|twice|more than once|"
                               r"\bsame\b|\bshar(?:e|es|ed|ing)\b|used (?:by|in|on|for) two")),
        Check("names a repeated id or row", r"\b(?:17|22|31|44)\b"),
    ],
    required_tools=[{"check_keys", "find_duplicates"}],
    truth={"duplicate_key_rows": 2, "groups": [[17, 31], [22, 44]]},
)


# ---------------------------------------------------------------- Excel workbook

STAFF_ORPHANS = {9: 7, 22: 8}  # rows 9 and 22 point at departments 7 and 8, which don't exist
STAFF_PLACEHOLDERS = {5: "TBD", 14: "TBD", 27: "TBA"}


def _build_workbook(folder: Path) -> None:
    import pandas as pd

    rng = random.Random(8)
    employees = []
    for i in range(1, 31):
        start = STAFF_PLACEHOLDERS.get(i) or (
            pd.Timestamp("2023-01-02") + pd.Timedelta(days=rng.randint(0, 700))
        )
        employees.append([i, STAFF_ORPHANS.get(i, rng.randint(1, 4)), start,
                          rng.randrange(38_000, 92_000, 1_000)])
    with pd.ExcelWriter(folder / "staff.xlsx", engine="openpyxl") as writer:
        pd.DataFrame({"id": [1, 2, 3, 4],
                      "department": ["Sales", "Finance", "Engineering", "Support"]}) \
            .to_excel(writer, sheet_name="departments", index=False)
        pd.DataFrame(employees, columns=["id", "department_id", "start_date", "salary"]) \
            .to_excel(writer, sheet_name="employees", index=False)


WORKBOOK = Scenario(
    id="excel_workbook",
    title="A two-sheet workbook with broken links and placeholder dates",
    prompt="Check staff.xlsx before I build our headcount report from it.",
    build=_build_workbook,
    expect=[
        Check("counts the 2 employees in departments that don't exist",
              near(number(2), r"orphan|n[o']?t exist|non-?existent|missing department|"
                              r"unknown department|no matching|invalid department")),
        Check("points at an orphaned row", r"\b22\b|\brows? 9\b"),
        Check("names the TBD placeholder", r"\bTB[DA]\b"),
    ],
    required_tools=[{"check_references"}],
    truth={"orphan_rows": sorted(STAFF_ORPHANS), "start_date_placeholders": 3},
)


SCENARIOS = [DUPLICATES, MISSING, ORPHANS, OUTLIER, PERSONAL, CLEAN, BAD_KEY, WORKBOOK]
BY_ID = {s.id: s for s in SCENARIOS}
