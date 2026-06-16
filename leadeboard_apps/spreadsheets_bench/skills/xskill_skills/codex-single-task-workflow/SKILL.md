---
name: codex-single-task-workflow
description: '处理 p3b 基准测试中 codex_single 类型评估任务的工作流：读取 workspace 中的 task.md 获取任务描述，检查 input.xlsx 等附件文件，调用匹配的
  Skill（如有）， 编写 solution.py 作为解决方案，并通过 python run_solution.py 进行本地验证。 典型触发表述： "Read task.md, inspect input.xlsx
  if useful, and write the final solution to solution.py. You may run python run_solution.py to validate
  the script locally." 适用于单轮次、无后续追问、需产出可执行 Python 脚本的 完整编码任务指派场景。需要文件 I/O 权限、Python 运行环境、以及 openpyxl/pandas
  等 xlsx 读取库。

  '
compatibility:
  environment: Python 3.8+；需安装 openpyxl 或 pandas（用于读取 input.xlsx）
  permissions: 允许读取 workspace 目录下所有文件；允许创建/修改 solution.py；允许运行 python run_solution.py
  negative_constraints:
  - 不适用于多轮次交互型任务（如对话调试、追问澄清后再编码）
  - 不适用于无需产出 .py 文件的自然语言问答场景
  - 不适用于需要调用外部 API 或网络服务的任务（除非 task.md 明确要求）
metadata:
  version: 1
  created: '2026-06-09'
  last_updated: '2026-06-09T19:20:32'
  source_atoms:
  - atom_traj_cc_codex_single_0cff92cd_0001
  - atom_traj_cc_codex_single_af58cd18_0001
  - atom_traj_cc_codex_single_ba6d8ed9_0001
---

# codex-single-task-workflow：p3b 单次编码评测任务

## 概述

本 Skill 定义了 p3b 基准测试中 **codex_single** 类型评估任务的标准执行流程。
它是一种**单轮次、端到端**的编码委托场景：用户给出明确的 workspace 文件和
任务描述，Agent 需要一次性产出可运行的 `solution.py` 并通过本地验证。

> 3/3 atoms 均显示相同的任务结构和提示模板，证明该流程是 codex_single
> 评测任务的标准化协议。见 `atom_traj_cc_codex_single_0cff92cd_0001` 等。

### 典型目录结构

```
<task_workspace>/
├── task.md             # 任务描述（必读）
├── input.xlsx          # 输入数据（可选检查）
├── ATTACHMENTS.md      # 附件说明文件（如果存在则需读取）
├── solution.py         # 待编写/产出的解决方案（目标文件）
└── run_solution.py     # 本地验证脚本（只运行，不修改）
```

### 核心原则

1. **先读后写**：先读 `task.md` 理解需求，再动手写代码
2. **复用 Skill**：如有描述匹配的 Skill，通过 Skill tool 调用并按其指引操作
3. **可验证**：产出的 `solution.py` 必须能通过 `python run_solution.py` 验证
4. **单轮完成**：一次提交即交付完整方案，不等待追问

---

## 阶段一：理解任务与工作区

1. **读取 `task.md`**
   - 从 workspace 根目录读取 `task.md`，完整理解任务目标、输入格式、输出要求
   - 注意 task.md 可能包含数据格式说明、算法约束、边界条件

2. **检查 `ATTACHMENTS.md`**
   - 如果 `ATTACHMENTS.md` 存在，读取它并检查其列出的本地文件
   - ATTACHMENTS.md 可能列出额外数据文件、参考代码或说明文档

3. **检查 `input.xlsx`**
   - 读取 `input.xlsx` 以了解输入数据的结构和内容
   - 检查列名、数据类型、缺失值等，为编码做准备

   > ⚠️ 所有 3 个 atom 的初始提示都明确包含 "inspect `input.xlsx` if useful"
   > 的表述，但检查与否取决于任务是否需要。见
   > `atom_traj_cc_codex_single_af58cd18_0001` 第 17 行。

---

## 阶段二：选择执行策略

1. **检查是否有匹配的 Skill**
   - 系统提示会说明 "Relevant Skills are available to you: when a Skill's
     description matches this task, invoke it via the Skill tool and follow
     its guidance."
   - 查看已加载的 Skill 列表，如果有描述匹配当前任务的 Skill，通过 Skill
     tool 调用并按指引操作

   > ⚠️ 3/3 atoms 均包含 Skill 调用指引，但 codex_single 任务本身也可能
   > 没有匹配的 Skill，此时应直接自行编码。不要虚构不存在的 Skill 调用。

2. **直接编码（或按 Skill 指引编码）**
   - 根据 `task.md` 的需求和 `input.xlsx` 的数据结构，编写 `solution.py`
   - 代码应包含必要的 import（如 `pandas` / `openpyxl` 用于读 xlsx）
   - 脚本应设计为可直接执行（`python solution.py`），读取输入、处理、输出结果

3. **不修改非目标文件**
   - 只创建/修改 `solution.py`，不要修改 `run_solution.py` 或 `task.md`
   - `run_solution.py` 是评测方提供的验证脚本，修改会破坏评测一致性

---

## 阶段三：验证与最终输出

1. **运行 `python run_solution.py` 进行本地验证**
   - 在 workspace 目录下执行 `python run_solution.py`
   - 确认运行无报错，输出结果符合预期
   - 如果验证失败，根据错误信息修正 `solution.py` 后重新验证

   > ⚠️ 所有 3 个 atom 的用户提示均明确包含 "You may run
   > `python run_solution.py` to validate the script locally"，这是标准
   > 验证步骤。见 `atom_traj_cc_codex_single_0cff92cd_0001` 第 18 行。

2. **输出最终回答**
   - 简要确认 `solution.py` 已编写
   - 总结实现方案和关键思路（算法、数据处理方式等）
   - 确保输出格式包含要求的 `<answer>...</answer>` 标签（如果 task.md 要求）

   > ⚠️ 系统提示要求 "Return only the final answer text, keeping any
   > required `<answer>...</answer>` tags exactly." 3/3 atoms 均有此要求，
   > 务必遵守标签格式。见 `atom_traj_cc_codex_single_ba6d8ed9_0001` 第 10 行。

---

## 常见注意事项

### 输入数据读取

`input.xlsx` 的读取推荐使用以下方式：

```python
import pandas as pd

df = pd.read_excel("input.xlsx")
# 或使用 openpyxl 直接操作
```

### 输出规范

- `solution.py` 应能在无额外参数的情况下独立运行
- 输出结果通常写入文件或打印到 stdout，具体格式以 `task.md` 要求为准
- 避免在 `solution.py` 中使用交互式输入（`input()`）

### 环境和依赖

- 运行环境 Python 3.8+
- 常用依赖：`pandas`, `openpyxl`, `numpy`, `scipy` 等科学计算库通常已预装
- 如需额外依赖，应在 `solution.py` 开头使用 `try/except` 处理导入失败
  并在最终回答中说明

### 单轮次约束

- 本流程假设**一次提交即完成**，没有追问机会
- 所有边界情况和错误处理应在 `solution.py` 中提前考虑
- 如有歧义，基于 `task.md` 和 `input.xlsx` 的实际内容做合理假设，并在
  最终回答中说明假设