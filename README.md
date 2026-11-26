# datalint-mcp

An MCP server that lets Claude audit the quality of your data. Point it at a CSV file and ask
"is this ready for training?" or "what's wrong with this table?", and Claude uses the server's
tools to profile the data, find missing and disguised-missing values, and spot duplicates.

> Work in progress. More tools (outliers, key integrity, personal-data detection, SQLite) and a
> model-in-the-loop eval suite are on the way.

## Tools

| Tool | What it answers |
|---|---|
| `load_dataset` | Loads a CSV or TSV file so the other tools can use it |
| `list_datasets` | Which datasets are loaded, and their size |
| `profile_dataset` | One overview per column: type, missing values, distinct values |
| `describe_column` | A deep look at one column: statistics, lengths, most common values, formatting problems |
| `check_missing` | Where values are missing, including disguised ones like `"N/A"` or `"-"` |
| `find_duplicates` | Exact duplicate rows, duplicates on chosen key columns, and rows that only differ by case or spacing |

## Safety by design

- **It only reads folders you allow.** Paths outside `DATALINT_ROOTS` (default: the folder the
  server starts in) are refused with a reason.
- **It never echoes whole rows.** Duplicate checks return row numbers, not values, and sample
  values are capped in length.
- **It's honest about cut output.** Long lists are paged, and every page says how many items
  exist and where the next page starts.

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

To try it, allow the `examples` folder and ask Claude to audit `customers.csv`: a ten-row file
with planted problems (exact and near-duplicate rows, `"N/A"` placeholders, stray spaces, an
empty column).

## License

MIT
