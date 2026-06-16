---
name: excel-openpyxl-cell-manipulation
description: 使用 openpyxl 读写 Excel 文件，对单元格进行读取结构、提取时间/日期、设置格式（数字格式/字体/颜色/对齐）、公式转值、跨 sheet 数据聚合等操作的技能。典型触发表述如
  "format the cells in column J to only display the standard time"（Cell-Level Manipulation）和 "match and
  sum values from columns repeated across multiple sheets ... insert a column called BALANCE"（Sheet-Level
  Manipulation）。需要 openpyxl 库、python3 环境，且工作区包含 task.md、input.xlsx、run_solution.py。
compatibility: 需要 python3 + openpyxl 库；工作区必须已有 task.md（描述指令）、input.xlsx（源文件）、run_solution.py（验证框架）。至少有一条负向硬约束：不适合处理
  >50MB 大型 xlsx 文件（openpyxl 全量加载内存）；不适合需要 Excel VBA 宏或公式保留的场景（本 skill 侧重用 Python 计算并写入值）。
metadata:
  version: 1
  created: '2026-06-09'
  last_updated: '2026-06-09T18:48:59'
  source_atoms:
  - atom_traj_cc_codex_single_27b77a6f_0001
  - atom_traj_cc_codex_single_d0af1edc_0001
---

# openpyxl Excel 单元格操作技能

本 skill 解决两类 Excel 自动化任务：**Cell-Level Manipulation**（对特定单元格范围做格式化/提取/计算）和 **Sheet-Level Manipulation**（跨 sheet 数据聚合 + 统一格式化）。核心原则是：先读 task.md 理解指令意图，再 inspect input.xlsx 摸清真实数据结构，然后按「读取 → 计算 → 写入」三步编写 solution.py，最后通过 run_solution.py 验证。

> ⚠️ 所有代码必须使用 `INPUT_PATH` 和 `OUTPUT_PATH` 变量（由 run_solution.py 注入），不要硬编码路径。不要假设行数/列数从 preview 截图中推断——始终遍历工作表的实际行范围（`ws.iter_rows()` / `ws.max_row`）。

---

## 阶段一：理解任务需求

1. **读取 task.md** — 用 Python 的 `openpyxl.load_workbook('input.xlsx')` 或 `pathlib.Path('task.md').read_text()` 提取指令。
   - 确定 **Instruction type**：`Cell-Level Manipulation` 或 `Sheet-Level Manipulation`
   - 确定 **Expected answer position**：如 `J2:J4` 或 `'COLLECTION'!A2:G9`
   - 记录关键要求：时间格式、颜色规则、对齐方式、空值处理、公式转值等。

   > ⚠️ task.md 中的 spreadsheet preview 是截断的（"preview may be truncated"），不要硬编码行数或假定数据在预览行结束。见 `atom_traj_cc_codex_single_27b77a6f_0001` 中 task.md 明确提示 "do not hardcode row counts"。

2. **读取 run_solution.py** — 了解验证框架。
   - 框架会向 solution.py 注入 `INPUT_PATH` 和 `OUTPUT_PATH` 变量。
   - solution.py 中**不应**自行定义 `INPUT_PATH` 或 `OUTPUT_PATH`（run_solution.py 会 strip 掉这些赋值行）。
   - 可选择性本地运行 `python run_solution.py` 验证。

   run_solution.py 的典型结构（见两 atom 均出现此框架）：
   ```python
   import pathlib, re, sys, traceback
   INPUT_PATH = "input.xlsx"
   OUTPUT_PATH = "output.xlsx"
   code = pathlib.Path('solution.py').read_text(encoding='utf-8')
   code = re.sub(r'^\s*(INPUT_PATH|OUTPUT_PATH)\s*=\s*.+$', '', code, flags=re.MULTILINE)
   globals_dict = {'__name__': '__main__', 'INPUT_PATH': INPUT_PATH, 'OUTPUT_PATH': OUTPUT_PATH}
   exec(compile(code, 'solution.py', 'exec'), globals_dict, globals_dict)
   ```

---

## 阶段二：检查 input.xlsx 实际结构

1. **用 bash + python3 内联脚本检查** — 加载 input.xlsx 并打印所有 sheet 的元数据和单元格内容。

   ```python
   import openpyxl
   wb = openpyxl.load_workbook('input.xlsx')
   print('Sheet names:', wb.sheetnames)
   for sn in wb.sheetnames:
       ws = wb[sn]
       print(f'\n=== Sheet: {sn} (dim={ws.dimensions}, max_row={ws.max_row}, max_col={ws.max_column}) ===')
       for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
           for cell in row:
               print(f'  {cell.coordinate}: value={cell.value!r}, type={type(cell.value).__name__}, number_format={cell.number_format!r}')
           print('---')
   ```

2. **关注的关键信息**：
   - **单元格值类型**：`datetime.datetime`（需专门处理时间提取）、`str`、`float`、`None`、公式字符串（如 `'=RIGHT(I2,8)'`）
   - **number_format**：确认当前格式（如 `'General'`、`'yyyy-mm-dd hh:mm:ss'`）
   - **数据范围**：`max_row` 和 `max_column` 决定遍历范围

   > ⚠️ 日期时间值在 openpyxl 中表现为 `datetime.datetime` 对象（而非字符串），直接用 `cell.value.hour`、`cell.value.time()` 等方法提取。见 `atom_traj_cc_codex_single_27b77a6f_0001` 中列 I 的 datetime 值（如 `2021-09-29 18:08:00`）。

---

## 阶段三：编写 solution.py — Cell-Level Manipulation 模式

适用于单元格级操作（时间格式化、数值计算、单个列的公式替换等）。

### 3.1 加载工作簿

```python
import openpyxl
from openpyxl.styles import numbers, Font, PatternFill, Alignment
from datetime import datetime

wb = openpyxl.load_workbook(INPUT_PATH)
ws = wb.active  # 或 wb['Sheet1']
```

### 3.2 遍历数据行并处理

```python
# 遍历所有行（从第2行开始，跳过表头）
for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
    source_cell = row[8]   # 列 I = index 8（0-based）
    target_cell = row[9]   # 列 J = index 9
    
    if isinstance(source_cell.value, datetime):
        # 提取时间并格式化
        time_val = source_cell.value.time()
        formatted_time = time_val.strftime('%I:%M:%S %p')  # 12h + AM/PM
        # 或 time_val.strftime('%H:%M:%S') 24h 格式
        
        target_cell.value = formatted_time
        target_cell.number_format = 'HH:MM:SS AM/PM'  # 设置单元格式
```

**关键 API**：
- `cell.value.time()` — 从 datetime 提取 time 对象
- `time.strftime(format)` — time → 格式化字符串
- `cell.number_format = 'HH:MM:SS AM/PM'` — 设置 Excel 数字格式
- `cell.value = cell.value` — 公式转值（覆盖公式为计算结果）

> ⚠️ 当原单元格包含公式（如 `=RIGHT(I2,8)`）时，openpyxl 读到的 `cell.value` 是公式字符串（如 `'=RIGHT(I2,8)'`），不是计算结果。需要在 solution.py 中直接计算所需值并写入。见 `atom_traj_cc_codex_single_27b77a6f_0001`。

### 3.3 保存

```python
wb.save(OUTPUT_PATH)
```

---

## 阶段四：编写 solution.py — Sheet-Level Manipulation 模式

适用于跨 sheet 数据聚合 + 格式化任务。

### 4.1 数据聚合（跨 sheet 按列匹配求和）

```python
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, numbers
from collections import defaultdict

wb = openpyxl.load_workbook(INPUT_PATH)

# 聚合所有 data sheet（排除 'COLLECTION' 等汇总 sheet）
data_sheets = [s for s in wb.sheetnames if s != 'COLLECTION']
aggregated = defaultdict(lambda: {'sale': 0, 'ret': 0})

for sn in data_sheets:
    ws = wb[sn]
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=False):
        ty = row[2].value   # 列 C = TY
        or_ = row[3].value  # 列 D = OR
        sale = row[4].value if row[4].value is not None else 0
        ret = row[5].value if row[5].value is not None else 0
        
        key = (ty, or_)
        aggregated[key]['sale'] += float(sale) if sale != '-' else 0
        aggregated[key]['ret'] += float(ret) if ret != '-' else 0
```

### 4.2 写入汇总 sheet 并设置格式

```python
coll_ws = wb['COLLECTION']
# 找到现有数据的最后一行
existing_rows = coll_ws.max_row

# 从现有数据行开始追加聚合结果
row_idx = existing_rows + 1
for (ty, or_), vals in aggregated.items():
    balance = vals['sale'] - vals['ret']
    
    # 写入数据
    coll_ws.cell(row=row_idx, column=1).value = <ITEM>
    coll_ws.cell(row=row_idx, column=2).value = <BR>
    coll_ws.cell(row=row_idx, column=3).value = ty
    coll_ws.cell(row=row_idx, column=4).value = or_
    coll_ws.cell(row=row_idx, column=5).value = vals['sale']
    coll_ws.cell(row=row_idx, column=6).value = vals['ret']
    
    bal_cell = coll_ws.cell(row=row_idx, column=7)
    # 公式转值：直接写入计算结果而非公式
    if balance < 0:
        bal_cell.value = balance
        bal_cell.font = Font(color='FF0000')  # 红色负数
    elif balance == 0:
        bal_cell.value = '-'  # 空值→连字符
    else:
        bal_cell.value = balance
    
    row_idx += 1
```

### 4.3 批量格式化

```python
# 字体设置
font_calibri = Font(name='Calibri', size=11, bold=False)

# 对齐设置
align_left = Alignment(horizontal='left')
align_right = Alignment(horizontal='right')

# 表头设置
for cell in coll_ws[1]:  # 第1行 = 表头
    cell.font = Font(name='Calibri', size=11, bold=False)  # 取消加粗
    cell.alignment = Alignment(horizontal='left')

# 按列设置对齐（示例：A/E/F/G 列右对齐，B/C/D 列左对齐）
for row in coll_ws.iter_rows(min_row=2, max_row=coll_ws.max_row):
    for col_idx in [0, 4, 5, 6]:  # A/E/F/G
        row[col_idx].alignment = Alignment(horizontal='right')
    for col_idx in [1, 2, 3]:     # B/C/D
        row[col_idx].alignment = Alignment(horizontal='left')
```

### 4.4 空值处理与特殊值

```python
# 空值/0 显示为连字符
for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
    for cell in row:
        if cell.value == 0 or cell.value is None:
            cell.value = '-'
```

> ⚠️ 注意：数值 `0` 和空值 `None` 要区分处理。如果指令要求 "empty cells should show a hyphen instead of a zero"，需要先检查是否有 0 值需要替换。见 `atom_traj_cc_codex_single_d0af1edc_0001`。

---

## 阶段五：验证与提交

1. **本地验证**（可选）：运行 `python run_solution.py` 检查是否成功生成 output.xlsx。
2. **检查 output.xlsx**：用 openpyxl 加载 output.xlsx 确认格式正确。
3. **最终回答**：在最终响应中确认 `solution.py` 已编写，并简要总结方法。

---

## 参考：常用 openpyxl 格式 API 速查

| 操作 | API | 说明 |
|------|-----|------|
| 字体 | `Font(name='Calibri', size=11, bold=False, color='FF0000')` | 名称/大小/加粗/颜色 |
| 对齐 | `Alignment(horizontal='left', vertical='center')` | left/right/center |
| 数字格式 | `cell.number_format = 'HH:MM:SS AM/PM'` | Excel 格式字符串 |
| 填充色 | `PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')` | 单元格背景色 |
| 字体颜色 | `Font(color='FF0000')` | 十六进制颜色码 |
| 列宽 | `ws.column_dimensions['A'].width = 15` | 设置列宽 |
| 行高 | `ws.row_dimensions[1].height = 20` | 设置行高 |

**时间格式化 strftime 格式对照**：
| 格式 | 输出示例 | 说明 |
|------|----------|------|
| `%H:%M:%S` | `18:08:00` | 24小时制 |
| `%I:%M:%S %p` | `06:08:00 PM` | 12小时制 + AM/PM |
| `%I:%M %p` | `06:08 PM` | 12小时制简写 |