---
name: xlsx
description: "Spreadsheet (.xlsx, .xlsm, .csv) creation, editing, and analysis by writing a single self-contained Python script. Use this skill whenever the agent must (1) read or analyze spreadsheet data, (2) modify cells, ranges, or whole sheets, (3) compute aggregations, sorts, filters, lookups, or derived values, or (4) produce a corrected workbook. The script computes every final value directly in Python with openpyxl/pandas and writes the literal result into the target cells; it does NOT emit Excel formula strings or rely on any external recalculation engine."
license: Proprietary. LICENSE.txt has complete terms
---

# Spreadsheet Manipulation by Direct Python Computation

## Overview

A user may ask you to create, edit, or analyze the contents of an `.xlsx` file.
You solve the task by writing **one self-contained Python script** that reads the
input workbook, performs the requested transformation, and writes the output
workbook. Every value you put in a cell must be the **final computed value**,
calculated in Python — never an Excel formula string.

**Primary libraries**: `openpyxl` (structure-preserving read/write) and
`pandas` (data transformation). Do not depend on any other third-party library.

## Core Principle: Compute Values in Python, Write Literal Results

The output workbook is checked by reading the literal cell values with
`openpyxl(data_only=True)`. A cell that contains an Excel formula string such as
`"=SUM(B2:B9)"` reads back as the formula text (or `None`), **not** the number,
because no spreadsheet application reopens the file. Therefore:

- **Do the arithmetic, sorting, lookups, grouping, and string work in Python.**
- **Assign the resulting Python value (a number, string, date, etc.) to the cell.**
- **Do NOT write `"=..."` formula strings into cells.**
- **Do NOT call LibreOffice, `recalc.py`, or any other recalculation step** — the
  result must already be correct the moment the script saves the file.

### WRONG — writing an Excel formula (reads back as text, scored incorrect)
```python
ws["B10"] = "=SUM(B2:B9)"          # cell holds the string "=SUM(B2:B9)"
ws["C5"] = "=(C4-C2)/C2"           # never gets evaluated
```

### CORRECT — compute in Python, write the literal value
```python
total = sum(ws[f"B{r}"].value or 0 for r in range(2, 10))
ws["B10"] = total                  # cell holds the number, e.g. 5000

c4, c2 = ws["C4"].value, ws["C2"].value
ws["C5"] = (c4 - c2) / c2          # cell holds the computed ratio
```

This applies to **all** derived results — totals, averages, percentages, ratios,
differences, rankings, lookups, conditional counts, concatenations, and so on.

## Common Workflow

1. **Explore** the input workbook: list `wb.sheetnames`, inspect headers, check
   `ws.max_row` / `ws.max_column` and the actual data extent.
2. **Write a single script** that defines (or receives) `INPUT_PATH` and
   `OUTPUT_PATH`, loads the workbook, computes the required values in Python, and
   writes them into the target cells/ranges.
3. **Iterate over the real data** — never hardcode row counts, column letters, or
   values taken from a preview; walk the actual rows/columns in the workbook.
4. **Preserve** every sheet, cell, and piece of formatting that the instruction
   does not ask you to change.
5. **Save** to `OUTPUT_PATH`.

## Library Selection

| Use case | Library |
|----------|---------|
| Preserve formulas already in the file, formatting, named ranges | `openpyxl` |
| Bulk transformation, aggregation, grouping, sorting | `pandas` for the compute, then write values back with `openpyxl` |
| Simple cell read/write | `openpyxl` |

**Warning**: `pandas.DataFrame.to_excel()` rewrites the whole sheet and destroys
existing formatting, formulas, and named ranges. When the task requires
preserving the rest of the workbook, load with `openpyxl`, compute (optionally
using pandas in memory), and assign the computed values cell-by-cell, then
`wb.save(OUTPUT_PATH)`.

## Reading values correctly

- To read **already-computed values** from an input file that may contain
  formulas, open it with `data_only=True`:
  `wb = openpyxl.load_workbook(INPUT_PATH, data_only=True)`.
  Note: `data_only=True` returns `None` for formula cells that were never cached,
  so prefer computing from the underlying raw data yourself.
- For pure data analysis you may use
  `pd.read_excel(INPUT_PATH, sheet_name=None)` to load every sheet as a dict of
  DataFrames, compute, and then write the results back through `openpyxl`.

## Script Template

```python
import openpyxl
import pandas as pd

INPUT_PATH  = "input.xlsx"    # the workbook to read
OUTPUT_PATH = "output.xlsx"   # where to write the result

wb = openpyxl.load_workbook(INPUT_PATH)
ws = wb.active                # or wb["SheetName"]

# --- compute final values in Python and assign them ---
# e.g. iterate real rows, aggregate, sort, look up, then write literals:
# ws["D2"] = computed_value

wb.save(OUTPUT_PATH)
```

## Output Requirements

- Save the result to `OUTPUT_PATH`.
- Place each computed result as a **literal value** in the target cell — no
  Excel formula strings, no post-hoc recalculation.
- Do not hardcode row counts or column letters; iterate over the actual rows and
  columns present in the workbook.
- Preserve sheets, cells, and formatting not mentioned in the instruction.

## Common Pitfalls to Avoid

- **Writing `"=..."` into a cell** — it will be scored against the literal value
  and fail. Compute the number in Python instead.
- **Relying on recalculation** — there is no spreadsheet engine in the loop; the
  saved file is read as-is.
- **Off-by-one ranges / 1-based indexing** — openpyxl rows and columns are
  1-based (`ws.cell(row=1, column=1)` is `A1`); pandas/iterables are 0-based.
- **Trusting a truncated preview** — the data may extend far below the previewed
  rows; always scan to `ws.max_row` / the real end of the data.
- **Division by zero / `None` cells** — guard denominators and treat empty cells
  as `0` or skip them as the instruction requires.
- **Destroying formatting with pandas** — use `openpyxl` to write back when the
  rest of the workbook must be preserved.
