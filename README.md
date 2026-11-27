# datalint-mcp

[![tests](https://github.com/asma-hachaichi/datalint-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/asma-hachaichi/datalint-mcp/actions/workflows/tests.yml)

An MCP server that lets Claude audit the quality of your data. Point it at a CSV file or a
SQLite database and ask "is this ready for training?" or "what's wrong with these tables?".
Claude uses the server's tools to profile the data, find missing values, duplicates, outliers,
broken keys and orphaned references, and spot personal data, without ever showing it.

> Work in progress. A Claude Code plugin and a PyPI release are on the way.

## Tools

| Tool | What it answers |
|---|---|
| `load_dataset` | Loads a CSV, TSV or SQLite file. A database loads every table, with its declared foreign keys |
| `list_datasets` | Which datasets are loaded, and their size |
| `profile_dataset` | One overview per column: real type, missing values, distinct values, personal data |
| `describe_column` | A close look at one column: statistics, lengths, common values, formatting problems |
| `check_missing` | Where values are missing, including disguised ones like `"N/A"` or `"-"` |
| `find_duplicates` | Exact duplicate rows, duplicates on key columns, and rows that differ only by case or spacing |
| `find_outliers` | Numbers far outside a column's usual range (interquartile range or z-score) |
| `check_keys` | Whether a column, or a set of columns, uniquely identifies every row |
| `check_references` | Orphans: values that point at rows missing from another table |
| `detect_pii` | Columns holding emails, phone numbers, card numbers, IBANs, IP addresses, names and more |

## Safety by design

- **It only reads folders you allow.** Paths outside `DATALINT_ROOTS` (default: the folder the
  server starts in) are refused with a reason.
- **It never changes your data.** Databases are opened read-only.
- **It keeps personal data out of the conversation.** Columns that look like personal data are
  flagged, and no tool ever returns their values. Card numbers and IBANs are confirmed with
  their checksums, so random IDs aren't mistaken for them.
- **It never echoes whole rows.** Duplicate and reference checks return row numbers, not values.
- **It's honest about cut output.** Long lists are paged, and every page says how many items
  exist and where the next page starts.

## The Claude skill

[`skills/datalint/SKILL.md`](skills/datalint/SKILL.md) teaches Claude how to run an audit:
which tool to call when, what to skip, and how to report the problems, most serious first,
with real numbers and a concrete fix for each.

## Evals: does Claude actually use the tools well?

Unit tests prove each tool returns the right numbers. They can't prove that a model, reading
only the tool descriptions and the skill, picks the right tools and reports what matters. The
eval suite checks that: a real Claude model audits datasets with planted problems through the
real server over stdio, and its final report is scored.

| Scenario | Planted problem | The report must |
|---|---|---|
| `duplicate_customers` | 3 exact duplicate rows, 2 that differ only by case or spacing | count the exact copies and mention the near-duplicates |
| `disguised_missing` | 9 incomes written as `N/A`, `-` or `unknown`, plus 3 blanks | name the column, the placeholder and the size of the gap |
| `orphaned_orders` | 4 orders pointing at customers that don't exist (SQLite) | count them and point at a row |
| `extreme_outlier` | a 999 °C reading among room temperatures | give the value and its row |
| `personal_data` | names, emails and phone numbers | flag them, and never let one appear in the transcript |
| `clean_data` | nothing | say it's ready, without inventing duplicates, outliers or orphans |
| `bad_key` | a primary key reused on 2 rows | answer no, and say which values repeat |

Each run is also scored on calling the right tool and staying within a tool-call budget.
The checks are plain patterns, not an LLM judge, so scores are cheap and repeatable.
Tests confirm the datalint tools find exactly the planted problems, so a failed eval always
points at the model's behaviour, not at broken test data.

Run them locally (each run calls the Claude API and costs money; the summary shows how much):

```powershell
.venv\Scripts\pip install -e ".[eval]"
$env:ANTHROPIC_API_KEY = "<your key>"
.venv\Scripts\python -m evals                      # every scenario, on claude-opus-5-5
.venv\Scripts\python -m evals --scenario bad_key --repeat 3
```

On GitHub, run the **evals** workflow by hand from the Actions tab, once the repository has an
`ANTHROPIC_API_KEY` secret. Unit tests run on every push and pull request.

## Run it locally

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

Add it to Claude Code, allowing it to read one folder:

```powershell
claude mcp add datalint -e DATALINT_ROOTS=C:\path\to\your\data -- C:\path\to\datalint-mcp\.venv\Scripts\datalint-mcp.exe
```

## Try it on the examples

Allow the `examples` folder, then ask Claude to audit one of these files:

- `customers.csv`: ten rows with exact and near-duplicate rows, `"N/A"` placeholders, stray
  spaces and an empty column.
- `shop.sqlite`: customers and orders, with orders pointing at customers that don't exist, an
  extreme order amount, and personal data. [`make_shop_db.py`](examples/make_shop_db.py)
  rebuilds it and lists every planted problem.

## License

MIT
