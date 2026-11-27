# datalint-mcp

An MCP server that lets Claude audit the quality of your data. Point it at a CSV file or a
SQLite database and ask "is this ready for training?" or "what's wrong with these tables?".
Claude uses the server's tools to profile the data, find missing values, duplicates, outliers,
broken keys and orphaned references, and spot personal data, without ever showing it.

> Work in progress. A model-in-the-loop eval suite and a Claude Code plugin are on the way.

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
