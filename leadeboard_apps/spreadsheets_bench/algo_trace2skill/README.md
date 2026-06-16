# 打榜镜像 · trace2skill

SpreadsheetBench × Skill 打榜算法镜像之一。用 **Trace2Skill 6 步错误驱动管线**
真训练出一个单文件技能 `skill.md`。镜像 tag：`localhost:5000/p_user1/algo-trace2skill:v1`。

## 定位

- **角色**：k8s Job 里的 **sidecar 容器**（`restartPolicy: Always`），与评测主容器
  共享 `/shared`。
- **职责**：跑完 6 步管线，把进化出的 `SKILL.md` 复制到 `/shared/skill/skill.md`，
  落 `DONE`，然后 `sleep infinity`（失败也不退出——sidecar 退出会被无限重启）。
- **技能约定**：**单文件 `skill.md`**，评测镜像以 *reference 模式* 注入 system prompt。

## 算法原理（Trace2Skill）

不同于 SkillOpt 的"文本梯度优化"，Trace2Skill 从 **agent 实际跑题的轨迹与错误**里
蒸馏经验：先让一个带预置技能的 CLI agent 去解题、产出轨迹，再对照官方评测找出错题，
对错题做 **agentic 错误分析**，最后把分析出的教训"进化"回技能正文。种子技能是
`spreadsheet_agent/skills/xlsx`（一套 Excel 操作技能），进化只深化这套 `xlsx` 技能。

## 训练流程（`entrypoint_train.sh` 6 步）

数据为 `/app/t2s_train`（烘焙的 `dataset.json`，**6 题**）；模型走 **openai_chat**
（`OPENAI_BASE_URL` + `OPENAI_API_KEY`），`generation_config` 固定
`temperature=1.0 top_p=1.0 timeout=600`。

1. **产轨迹** `run_spreadsheetbench.py`：`cli_skill_preloaded` agent 带种子技能解 6 题，
   `--max_turns 30`，输出轨迹日志（markdown）。
2. **评测** `evaluate_with_official.py`：跑官方判分（non-fatal，失败继续）。
3. **analyze_results** `analyze_results.py`：汇总评测结果（non-fatal）。
4. **错误分析** `analysis/run_error_analysis.py`：对错题做 agentic 分析（`--max_turns 30`）。
5. **解析** `analysis/parse_error_analysis_outputs.py`：把错误分析整理成结构化 JSON。
6. **进化** `skill_evolver.run_parallel_skill_evolution`：error-driven 把教训写回
   `xlsx/SKILL.md`（`--prompt generic --patch-pipeline json --batch-size 1 --seed 42`）。

产出：`evolution/skills/xlsx/SKILL.md` → `cp` 到 `$SKILL_DIR/skill.md`，写
`ALGO=trace2skill`、`touch DONE`。第 1/4/6 步 non-zero 都"继续"，只有最终拿不到
非空 `SKILL.md` 才不写 `DONE`（避免交付空技能）。

## 训练规模

**无 `TRAIN_SCALE` 开关**——6 题已烘焙进 `t2s_train/dataset.json`。要扩大规模需替换
`t2s_train/dataset.json` 为更多题并重建镜像；`MAX_TURNS` 可调每题 agent 的轮数预算。

## 打榜环境变量

提交时经 `env_text` 注入；密钥经 `K8S_JOB_SECRET` Secret 注入，勿明文写 `env_text`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WORKERS` | `4` | 各步并发（产轨迹 / 错误分析 / 进化的 worker 数） |
| `EVAL_MODEL` | `deepseek-v4-flash` | 产轨迹 + 错误分析 + 进化用的模型名 |
| `MAX_TURNS` | `30` | 单题 agent 轮数预算（步 1 产轨迹 + 步 4 错误分析） |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `DEEPSEEK_API_KEY` | （必填，Secret 注入） | 映射到 `OPENAI_API_KEY`；缺失入口报错 |
| `SKILL_DIR` | `/shared/skill` | 技能产出目录（与评测容器约定，勿改） |

## 构建

```bash
bash build.sh          # 从 ../../../vendor/{Trace2Skill,data_root} 暂存 _ctx 并 build+push
```

`vendor/Trace2Skill` 是去掉 `data/.git` 的精简源码；`_ctx/` 为构建上下文（gitignore）。
