# Spreadsheet Manipulation Skill (xlsx)

## Overview
This skill guides agents in manipulating Excel (.xlsx) spreadsheets using Python.

**Primary libraries**: `openpyxl` (structure-preserving read/write), `pandas` (data transformation).
Never use any other third-party libraries.

---

## Common Workflow

### Loading and Exploring the Workbook

- **Loading with `data_only=True`**: If the workbook contains formulas whose computed values you need (e.g., for summation, condition checking, or sorting), load the workbook a second time with `data_only=True` to access cached computed values. Keep the normal load for writing back to preserve formulas:
  ```python
  wb_data = openpyxl.load_workbook(INPUT_PATH, data_only=True)
  ws_data = wb_data.active
  wb = openpyxl.load_workbook(INPUT_PATH)  # for writing
  ```
- **Using Python Data Structures**: For tasks requiring summing, filtering, or matching across rows or sheets, read source data into in-memory Python structures (dict, list of tuples). Build lookup/aggregation maps before writing output cells. Iterate with `ws.iter_rows(values_only=True)` for efficient reading.
- **Reading a Range of Cells**:
  ```python
  rows = []
  for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
      rows.append(row)
  ```
- **Dynamic Column Identification by Header**:

- **Exploring Lookup Tables and Grids**: When the task involves a lookup table (e.g., buckets for thresholds, payoff matrices), examine ALL rows and columns of the source data before coding.  Print the full table or iterate over the range to understand the structure.  Build the lookup logic in Python only after you have a clear mental model of the row/column layout and bucket boundaries.  Do not assume the grid shape from the preview alone.

- **Dynamic Row Identification by Label**: When data rows have labels (e.g., "Opening Bal", "Debits"), build a mapping from label to row number:
  ```python
  row_labels = {}  # label -> row number
  for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=1, values_only=True):
      if row[0] is not None:
          row_labels[str(row[0]).strip()] = row_num
  target_row = row_labels.get('Desired Label')
  ```
  ```python
  header = [cell.value for cell in ws[1]]
  try:
      col_idx = header.index("Desired Column Name") + 1   # 1‑based
  except ValueError:
      col_idx = None   # handle missing column gracefully
  ```
- **Handling None / Empty Cells**: Always check `cell_value is not None` and `isinstance(cell_value, str)` before string operations.

1. **Explore** the input file: list sheets, inspect headers, check dimensions.
2. **Write `solution.py`** with `INPUT_PATH` and `OUTPUT_PATH` defined at the top.
3. **Execute** `python solution.py` and verify the output file was created.
4. **Confirm** the target cells/range contain the expected values.

---

## Library Selection

| Use case | Library |
|----------|---------|
| Preserve formulas, formatting, named ranges | `openpyxl` |
| Bulk data transformation, aggregation, sorting | `pandas` → write back with `openpyxl` |
| Simple cell read/write | `openpyxl` |

**Warning**: `pandas.to_excel()` silently destroys existing formulas and named ranges.
When writing back to a spreadsheet that contains formulas, always use `openpyxl.save()`.

---

## solution.py Template

```python
import openpyxl
import pandas as pd

INPUT_PATH  = "..."   # set to the actual input path
OUTPUT_PATH = "..."   # set to the actual output path

wb = openpyxl.load_workbook(INPUT_PATH)
ws = wb.active  # or wb["SheetName"]

# --- perform manipulation ---

wb.save(OUTPUT_PATH)
```

---

## Output Requirements

### Handling Cell Values Safely
Cell values can be `None`, numeric, string, `datetime`, or an Excel error (e.g., `#N/A`, `#VALUE!`), which appears as a string starting with `'#'`. When checking whether a cell is "blank" or contains a special placeholder (like `"-"`, `"0"`, `"$"`), check for `None` and for error strings:

```python
def is_bad(value):
    """Return True if the value is blank or a placeholder like '-' or '0'."""
    if value is None:
        return True
    if isinstance(value, str) and value.startswith('#'):
        return True  # covers #N/A, #VALUE!, etc.
    if isinstance(value, (int, float)) and value == 0:
        return False  # numeric zero is valid unless context says otherwise
    return str(value).strip() in {"-", "", "$", "$0", "$0.0"}
```

When reading from a sheet that may contain formulas, use `data_only=True` (see Loading and Exploring the Workbook).
Cell values can be `None`, numeric, string, `datetime`, or an openpyxl `CellError` object (e.g., `#N/A`, `#VALUE!`). When checking whether a cell is "blank" or contains a special placeholder (like `"-"`, `"0"`, `"$"`), also check for `None` and for error types:
```python
from openpyxl.cell.cell import CellError
def is_bad(value):
    return value is None or isinstance(value, CellError) or str(value).strip() in {"-", "0", "$", "$0", "$0.0"}
```
When reading from a sheet that may contain formulas, use `data_only=True` (see Loading and Exploring the Workbook).

### Common Patterns
- **Build lookup dictionaries**: When a reference table is present, read it into a Python dictionary or set for fast lookups.
- **Date and time filtering**: Convert date/time cell values to Python `datetime` objects. Use comparison operators directly. For rolling windows, collect data into a list then iterate.
- **Write computed values, not formulas**: When the task asks for calculated results, compute in Python and write the resulting value directly. Avoid writing Excel formulas as strings unless explicitly required.
- **Cell formatting**: Use `openpyxl.styles.Font`, `Alignment`, `PatternFill` etc. Apply formatting after writing values. When copying formatting from another cell, use `copy(src_cell.font)` etc.

### Formatting Cells
```python
from openpyxl.styles import Font, Border, Side, Alignment, PatternFill

thin_side = Side(style='thin')
border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
font = Font(name='Calibri', size=11, bold=False)

for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=3, max_col=3):
    for cell in row:
        cell.border = border
        cell.font = font
```
For background colors:
```python
fill = PatternFill(start_color="00FF00", end_color="00FF00", fill_type="solid")
cell.fill = fill
```
For custom number formats, set `cell.number_format` as needed.

### Deleting Rows, Columns, or Sheets
- When deleting multiple rows, **iterate from bottom to top** to avoid index shifting:
  ```python
  for row in range(ws.max_row, 0, -1):
      if condition_to_delete(row):
          ws.delete_rows(row)
  ```
- For columns, use `ws.delete_cols(start_col, count)`. Determine ranges dynamically based on search.
- To delete a sheet: `del wb["SheetName"]` after checking existence with `if "SheetName" in wb.sheetnames:`.

### Sorting Data Without Losing Formulas
Read data rows into a list, sort with Python's `sorted()`, then write back:
```python
data = []
for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=False):
    data.append([cell.value for cell in row])
data.sort(key=lambda r: (r[2], r[5]))
for i, row_data in enumerate(data, start=2):
    for j, val in enumerate(row_data, start=1):
        ws.cell(row=i, column=j).value = val
```
Do not use `pandas` for sorting if the sheet contains formulas.

- Save the result to `OUTPUT_PATH`.
- Do not hardcode row counts or column letters — iterate over actual rows in the workbook.
- Preserve sheets and cells not mentioned in the instruction.

---

## Handling Merged Cells

- Before writing to a cell, check if it belongs to a merged range. Use `ws.merged_cells.ranges` to get all merged ranges.
- If the target cell is inside a merged range, **write only to the top-left cell** of that range (the only writable cell). Example:
  ```python
  for merged_range in ws.merged_cells.ranges:
      if merged_range.min_row <= target_row <= merged_range.max_row \
         and merged_range.min_col <= target_col <= merged_range.max_col:
          # Write to the top-left cell of the merged range
          ws.cell(row=merged_range.min_row, column=merged_range.min_col).value = new_value
  ```
- If you need to write to individual cells inside a merged range, **unmerge first** with `ws.unmerge_cells(str(merged_range))`, then write to each cell.
- Do not attempt to assign a value to a `MergedCell` object – this raises an `AttributeError: read-only`.

- **Default values for missing lookups**: When performing lookups (dictionary mapping, INDEX/MATCH simulation), write the appropriate default value when no match is found.  The default depends on the context: use `0` for numeric aggregations, `'-'` for display zeros, `'#N/A'` for lookup errors, or an empty string.  Never leave a cell as `None` unless that cell was already `None` in the original and the task says to keep it unchanged.

- **Cross-sheet data flow pattern**: When the output target is on a different sheet from the source data:
  1. Load workbook with `data_only=True` to read source sheet values.
  2. Load workbook normally to preserve formulas on all sheets.
  3. Identify the target sheet by name (not `active`).
  4. Build lookup maps from the source sheet, keyed by matching criteria.
  5. Iterate over the output range and populate cells using the lookup maps.
  6. Save with the normal workbook (preserves formulas elsewhere).
  Never assume `active` is correct; always use `wb['SheetName']`.

- **Normalize strings before comparing them**: When performing lookups or matching (e.g., VLOOKUP simulation, keyword matching), always normalize both the lookup keys and the reference data. Common normalizations: `str(value).strip().lower()`, removing apostrophes (`"'"`), periods, and extra whitespace. Build the lookup dictionary using the normalized key to ensure consistent matches.
  ```python
  def normalize(s):
      if s is None: return None
      return str(s).strip().lower().replace("'", "").replace(".", "")
  lookup = {normalize(row[0]): row[1] for row in ws_data.iter_rows(min_row=2, values_only=True)}
  target = lookup.get(normalize(ws.cell(row=r, column=c).value), '#N/A')
  ```

- **Conditional cell value update based on another column**: When a task says "if column X equals some text and column Y is a whole number, change column X to Z", iterate over data rows, read both columns, evaluate the condition with Python logic, and write the new value to the target cell. Use `data_only=True` if the condition depends on formula results. Handle cell values that may be strings, numbers, or `None`.
  ```python
  for row in range(2, ws.max_row + 1):
      col_e = ws_data.cell(row=row, column=5).value
      col_f = ws_data.cell(row=row, column=6).value
      if col_e and str(col_e).strip() == "Georgia Its Tax":
          if isinstance(col_f, (int, float)) and col_f == int(col_f):
              ws.cell(row=row, column=5).value = "Georgia WH Tax"
          else:
              ws.cell(row=row, column=5).value = "Georgia Sales Tax"
  ```

- **Search for text in a column for structural operations**: When a task requires deleting rows above a certain text anchor or inserting/moving a column based on a header value, first search the relevant column (case-insensitive) to find the target row or column index. Then perform the structural change: use `ws.delete_rows(start_row, count)` from top to bottom (or bottom to top if deleting multiple), and `ws.insert_cols(target_col)` / `ws.move_range()` for columns. Preserve formatting by copying cell styles when needed.
  ```python
  # Find row with "Invoice No." (case-insensitive)
  for row_num in range(1, ws.max_row + 1):
      val = ws.cell(row=row_num, column=1).value
      if val and str(val).strip().lower() == "invoice no.":
          anchor_row = row_num
          break
  if anchor_row and anchor_row > 1:
      ws.delete_rows(1, anchor_row - 1)
  ```

- **Filtering rows by condition and writing results to a target range**: When the task asks to "list all branches that are not ticked" or "show only unique values from a column", first read the source data, apply the filter in Python (list comprehension, set for uniqueness), then write the filtered values to the target range starting at a specific row. Preserve the target sheet's other contents. Handle `None` values and empty cells gracefully.
  ```python
  # Example: list unique values from Sheet2 to Sheet1 B2:B150
  wb_data = openpyxl.load_workbook(INPUT_PATH, data_only=True)
  ws2 = wb_data["Sheet2"]
  values = [ws2.cell(row=r, column=1).value for r in range(2, ws2.max_row + 1) if ws2.cell(row=r, column=1).value]
  unique_vals = list(dict.fromkeys(values))  # preserve order
  # Write to target
  wb = openpyxl.load_workbook(INPUT_PATH)
  ws1 = wb["Sheet1"]
  for i, val in enumerate(unique_vals, start=2):
      ws1.cell(row=i, column=2).value = val
  ```

- **ID/Name replacement across sheets**: When IDs in one sheet must be replaced by names from another sheet, read the entire lookup table into a dictionary (key=ID, value=Name) using `data_only=True` if the source sheet may contain formulas. Then iterate over every cell in the target columns, check if the cell value is in the dictionary, and replace it with the corresponding name. Only change the specified columns; leave all other cells unchanged.

- **When a task says 'adjust the formula' or 'fix the formula'**: Do not attempt to write an Excel formula string. Read all necessary data (using `data_only=True` for formulas), compute the intended result in Python, and write the resulting value directly. This is often required even when the user explicitly mentions a formula (e.g., 'my current formula is...').

- **Composite Key Lookups (tuple keys)**: When matching or aggregating across multiple columns (e.g., (TY, OR) or (InvoiceNo, Amount)), use a tuple as dictionary key:
  ```python
  lookup = {}  # (colA, colB) -> value
  for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
      key = (row[col_a_idx], row[col_b_idx])
      lookup[key] = sum_values
  ```
  This works for grouping, cross‑sheet matching, and deletion conditions.

- **First‑Occurrence Group Aggregation**: When the result for a group should appear only on the first row of that group, compute the aggregated value for each group, then track the first row seen for that key and write only there:
  ```python
  group_sums = {}
  first_rows = {}
  for row_num, key, value in data:
      group_sums[key] = group_sums.get(key, 0) + value
      if key not in first_rows:
          first_rows[key] = row_num
  for key, total in group_sums.items():
      ws.cell(row=first_rows[key], column=col).value = total
  ```
  For the remaining rows of each group, leave the cell empty (or as required by the task).

- **Aggregating data by group**: When the task requires summing, counting, or averaging values grouped by a category (e.g., material type, salesperson), read all source rows into a dictionary keyed by the grouping column(s). Accumulate the aggregated value (e.g., sum of width*height) in the dictionary values, then write the results to the output range.
  ```python
  from collections import defaultdict

  wb = openpyxl.load_workbook(INPUT_PATH, data_only=True)
  ws = wb.active
  header = [cell.value for cell in ws[1]]
  group_col_idx = header.index('Mtrl') + 1
  value_col_idx = header.index('Value') + 1  # or compute from width*height

  totals = defaultdict(float)
  for row in ws.iter_rows(min_row=2, values_only=True):
      group = row[group_col_idx-1]
      val = row[value_col_idx-1]
      if group is not None and val is not None:
          totals[group] += val

  # Write totals to output (e.g., starting at H2)
  for i, (group, total) in enumerate(totals.items(), start=2):
      ws.cell(row=i, column=8).value = total
  ```
  For multi-column computations (width * height), compute within the loop:
  ```python
  areas = defaultdict(float)
  for row in ws.iter_rows(min_row=2, values_only=True):
      mtrl = row[0]
      w, h = row[1], row[2]
      if mtrl is not None and w is not None and h is not None:
          areas[mtrl] += w * h
  ```

- **Cross-referencing delimited cell values**: When a cell contains multiple values separated by a delimiter (e.g., semicolon), split the cell, look up each part in a reference table, collect unique results (e.g., group names), sort if required, and join them into a single string. Handle edge cases like trailing delimiters and empty codes.
  ```python
  def lookup_cell(cell_value, lookup_dict):
      if cell_value is None:
          return ''
      codes = [c.strip() for c in str(cell_value).split(';') if c.strip()]
      groups = set()
      for code in codes:
          group = lookup_dict.get(code)
          if group:
              groups.add(group)
      sorted_groups = sorted(groups)
      return ', '.join(sorted_groups) if sorted_groups else ''
  ```

<!-- SLOW_UPDATE_START -->
## Critical Rules for Correct Outputs

1. **Never write Excel formulas or VBA code.** Even if the user explicitly asks for 'a formula', 'VBA code', or 'a macro', do NOT write a formula string into any cell. Instead, read all necessary input data (using `data_only=True`), perform the calculation in Python, and write the computed value directly. The evaluation always checks the cell's computed value, not the formula string. Exception: only if you have confirmed that the evaluation checks the formula string itself (extremely rare).

2. **Always identify the correct sheet by name from `wb.sheetnames`.** Never rely on `wb.active`. Examine headers to confirm the right sheet.

3. **For fixed cell ranges specified by the user (e.g., E2:E15), write to every cell in that exact range.** Use `for row in range(start, end+1):` to cover the entire range, even if some rows appear empty.

4. **After deleting rows, recompute all derived values for the remaining rows.** Deleting does not automatically update computed columns.

5. **Separate reading and writing into two distinct phases.** First, read all source data using `data_only=True` into Python structures. Then write all output cells.

6. **For string matching, use exactly the method described: `startswith()`, `in`, or exact equality.** Pay attention to special characters (fullwidth commas, punctuation) as written by the user.

7. **If your first attempt fails, recalculate in Python and write values. Do not resort to writing formulas.**

8. **When simulating lookup functions (INDEX/MATCH, VLOOKUP) and a lookup key has no match, write the string `'#N/A'` explicitly into the cell, not `None`.**

9. **When a task says to 'change the text in column X to Y', replace the entire cell value with Y. Do not concatenate, append, or modify the existing text.** For example, if the task says 'change Georgia Its Tax to Georgia WH Tax', set `cell.value = "Georgia WH Tax"` unconditionally after checking conditions. Never keep parts of the original value.

10. **When a task involves a complex text transformation described in words (e.g., 'move the fifth letter to the front'), write a Python function that implements the exact algorithm step by step. Test it on a sample value before applying to all rows. Use zero-based indexing for Python strings. Print the result for the first row to verify it matches the expected output before writing to all cells.**

11. **When a task involves parsing a column of delimited values and expanding them into multiple rows/columns, first print all unique values in the input to understand the structure. Then generate the full Cartesian product of the unique values per field. Write every combination as separate rows.**

12. **When aggregating data across multiple sheets, build a single comprehensive dictionary keyed by the join columns. For numeric aggregations, ensure the result is a number (int/float), not a string. Use `data_only=True` for all source reading.** Carefully implement any filtering conditions (warehouse, date, user, error codes) by reading columns by header name, not hardcoded indices.

13. **Always write placeholder strings (like `'-'`, `'#N/A'`, `''`) when the expected output requires them for missing data. Never leave cells as `None` if the output should contain a placeholder.**

14. **Do NOT import `CellError` from `openpyxl.cell.cell`.** That import may not exist in all openpyxl versions. Instead, detect Excel errors by checking if the value is a string starting with `'#'` (e.g., `'#N/A'`, `'#VALUE!'`). Use: `if isinstance(value, str) and value.startswith('#'):`.

15. **For conditional updates based on multiple columns (e.g., change column E based on column F being a whole number):** 
    - Read both columns from the data_only workbook.
    - Check that column F is a number (int or float) and compare to its integer value: `isinstance(val_f, (int, float)) and val_f == int(val_f)`.
    - Write the new value to the target cell using the normal (formula-preserving) workbook.
    - Always replace the entire cell value; do not concatenate or modify existing text.

16. **When a task involves an iterative balancing algorithm (e.g., add/subtract residuals across units respecting min/max constraints):** 
    - Read all unit columns and the residual column into a list.
    - Use a loop that adjusts values one unit at a time, checking min/max after each change.
    - After the loop, write back all adjusted values.
    - Print intermediate values to debug the algorithm before writing.

17. **For any task that asks for a formula to populate cells based on input from another cell (e.g., 'make cell J23 replicate the value in I12', 'populate classes based on cycle day'):** 
    - Do NOT write formulas like `=IF(...)` or `=INDEX(...)`.
    - Instead, read the input values (e.g., I12), compute the desired result in Python, and write the result directly to the output cell.
    - If the task involves a lookup table (e.g., mapping cycle day to classes), read the entire lookup table into a dictionary and then populate the output cells.
<!-- SLOW_UPDATE_END -->
