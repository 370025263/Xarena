# 榜单评测镜像 · spreadsheet-eval

SpreadsheetBench × Skill 的**榜单评测镜像**（评测主容器）。它不训练，只**接收**打榜算法
交付的技能、在 28 题测试集上跑评测、把可复现的数值指标与可追溯的过程回传后端。
镜像 tag：`localhost:5000/l_creator/spreadsheet-eval-{single,multi,react}:dsv4flash-10pct`。

## 定位

- **角色**：k8s Job 里的**评测主容器**，与打榜算法 sidecar 共享 `emptyDir` 卷 `/shared`。
- **职责**：等算法把技能写到 `/shared/skill`（`DONE` 标记）→ 跑 SkillOpt `eval_only.py`
  → 解析结果 → 把 metrics + 每题 eval_details `POST` 给指北针后端。评测完容器正常退出。
- **一镜像一模式**：`TARGET_MODE` 由 build-arg 烘焙，打三个镜像各跑一种 mode。

## 三种评测模式（TARGET_MODE）

| mode | 后端 | 说明 |
| --- | --- | --- |
| `single` | `claude_code_exec` | with-harness（claude CLI + harness），走 Anthropic 兼容端点 `…/anthropic` |
| `multi` | `openai_chat` | direct-chat codegen（`env.mode=multi`），一次直出解法 |
| `react` | `openai_chat` | react loop（`env.mode=react`），多轮工具调用 |

## 技能两种约定（自动识别）

`_resolve_skill()` 按优先级判定（见 `evaluator.py`）：

- **multi**：`/shared/skill/skills/*/SKILL.md` 存在 → xskill 多技能文件夹。拷进
  `.claude/skills`，`XSKILL_SKILL_MODE=native`，agent 按需用 Skill 工具。**仅支持
  `single` 模式**（多技能文件夹 + harness 才有 Skill 工具）。
- **single**：`/shared/skill/*.md` 取单文件（skillopt/trace2skill）→ reference 模式注入
  system prompt，三种 mode 都适配。

## 回传契约（逐字沿用"黄金" evaluator，不可改字段结构）

- 成功：`POST {API_INTERNAL_URL}/api/internal/submission/{SUBMISSION_ID}/score`，body
  `{metrics, eval_details, status:"Succeeded"}`。
- 失败：`{status:"Failed", metrics:{score:0, error}}`。
- evaluator **无改字段结构权限**，只回传数据、由后端落库。`SESSION.trust_env=False`
  确保回传走集群内网、不被 http 代理拦。

`metrics` 含 `score`(=hard×100)/`pass_rate_pct`/`hard`/`soft`/`n_tasks`/`n_pass`/
`n_fail`/`mode`/`model`/`skill_convention`/`split`/`dataset`。每条 `eval_details`
含 `question_id`/`gold_answer`(answer_position)/`pred_answer`(PASS|FAIL + cases)/
`is_correct`，`extra` 里带 `task_md`/`solution_py`/`chatmessage_log`/`instruction_type`
等过程证据，供榜单详情页"查哪题错了、为什么错、当时环境如何"。

## 数据集

`bench_test_split`（= `data/test_10pct_split`）：train 80 / val 40 / **test 28**。评测只跑
**test 28 题**（SpreadsheetBench 原集的 10% 抽样）。SpreadsheetBench 工作簿（26M）烘焙为
`/data`。判分为官方 hard-match（每题多 case 全过才算对）。

## 打榜（评测）环境变量

`SUBMISSION_ID`/`LEADERBOARD_ID`/`API_INTERNAL_URL` 由框架在创建 Job 时注入；密钥经
`K8S_JOB_SECRET` Secret 注入。`getenv_nonempty()` 保证空串不覆盖默认值。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TARGET_MODE` | `single`（build-arg 烘焙） | `single`/`multi`/`react` |
| `EVAL_MODEL` | `deepseek-v4-flash` | 评测目标模型名 |
| `WORKERS` | `3` | `eval_only.py` 并发 |
| `EXEC_TIMEOUT` | `600` | 单题执行超时秒数 |
| `SKILL_DIR` | `/shared/skill` | 等算法交付技能的共享目录 |
| `SKILL_WAIT_TIMEOUT` | `21600`(6h) | 等 `DONE` 标记的最长秒数，超时回传 Failed |
| `SPLIT_DIR` | `/bench/data/test_10pct_split` | 测试 split（烘焙） |
| `DATA_ROOT` | `/data` | SpreadsheetBench 工作簿根（烘焙） |
| `API_INTERNAL_URL` | `http://leaderboard-api-svc:80` | 后端回传地址 |
| `SUBMISSION_ID` / `LEADERBOARD_ID` | （框架注入） | 回传定位用 |
| `TARGET_ENDPOINT` | `https://api.deepseek.com` | multi/react 的 OpenAI 兼容端点 |
| `TARGET_API_KEY` | `$DEEPSEEK_API_KEY` | multi/react key |
| `ANTHROPIC_BASE_URL` | `https://api.deepseek.com/anthropic` | single 的 Anthropic 兼容端点 |
| `ANTHROPIC_AUTH_TOKEN` | `$DEEPSEEK_API_KEY` | single 的 key |
| `CLAUDE_BIN` | （PATH 自动解析） | single 的 claude 绝对路径，留空则 `which claude` |
| `EVAL_FORCE_PROXY` | `0` | =1 才启用内网 http 代理（黄金逻辑，外网部署用不到） |

> `IS_SANDBOX=1` 已烘焙进镜像：评测容器以 root 跑，single 模式的 claude 需要它才接受
> `--dangerously-skip-permissions`。

## 构建

```bash
bash build.sh                 # 暂存 _ctx 并构建 single+multi+react 三镜像
bash build.sh single          # 只构建某一种
bash build_single.sh          # 等价单模式脚本（_ctx 缺失时自动从 vendor/ 暂存）
```

`_ctx/`（`vendor/{SkillOpt,data_root}` 暂存物）为构建上下文，已 gitignore。
