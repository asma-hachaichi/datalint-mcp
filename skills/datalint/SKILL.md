---
name: datalint
description: Audit the quality of a dataset with the datalint MCP tools. Use when the user asks whether data is clean, ready for analysis or training, or what is wrong with a CSV, TSV or SQLite file, or asks about missing values, duplicates, outliers, keys, broken references or personal data in their data.
---

# Auditing data with datalint

The datalint tools read a dataset and report problems. They never change data and never show
whole rows. Your job is to run the right checks, then turn the numbers into a short, honest
report the user can act on.

## Run the audit in this order

1. **Load.** Call `load_dataset` with the path the user gave. A SQLite database loads every
   table, so you can audit them together. If the path is refused as outside the allowed
   folders, tell the user which folder to add to `DATALINT_ROOTS`. Don't try other paths.
2. **Profile.** Call `profile_dataset` on each dataset. Note its size, duplicate rows, each
   column's `type`, its `missing_pct`, and any `personal_data`.
3. **Follow the signals.** Call only what the profile points to:
   - Missing above a few percent, or any `disguised_missing`: call `check_missing`.
   - `duplicate_rows` above zero, or a column that should be unique: call `find_duplicates`,
     with `columns` set to the natural key (an email, an order number).
   - Numeric columns: call `find_outliers` once without a column, then with `column` for any
     that stand out.
   - `numeric_as_text`, odd distinct counts, or stray spacing: call `describe_column`.
   - Several related tables: call `check_keys` on each table, then `check_references`.
   - Any `personal_data`: call `detect_pii` for the full picture.
4. **Report** (see below). Don't call every tool on every column just to be thorough. Stop
   when the next call wouldn't change what you tell the user.

## Writing the report

- **Lead with a verdict in one sentence:** ready to use, usable after fixes, or not
  trustworthy yet.
- **List problems most serious first.** Broken references and invalid keys come first, because
  joins and counts will be wrong. Next come duplicates and heavy missing data, because
  statistics will be biased. Format problems such as numbers stored as text, stray spaces and
  case variants are last.
- **Use the tools' numbers, with their scale:** "142 of 5,000 orders (2.8%) point at customers
  that don't exist", not "some orders are orphaned". Never round away a problem, and never
  invent a count a tool didn't return.
- **Name the place:** the column, and up to five row numbers. Say that row numbers count data
  rows from 1, so row 1 is the first line after the header.
- **Give each problem a concrete fix:** "trim spaces in `country` and lowercase it",
  "decide whether rows 5 and 10 should be deleted or their customers restored".
- **Outliers are unusual, not wrong.** Present them as values to check, unless the column's
  meaning makes them impossible, like a negative age.
- **Personal data:** say which columns hold it and what kind, and suggest masking or removing
  it before sharing the data. Never ask a tool to reveal those values, and never guess them.

## When results are paged

If `page.next_offset` is not null, there is more. Fetch further pages only when the remaining
items could change your report. Otherwise, say how many you didn't look at.
