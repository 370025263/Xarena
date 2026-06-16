# 打榜镜像 · xskill

SpreadsheetBench × Skill 打榜算法镜像之一。用 **xskill daemon** 从 agent 轨迹里在线
蒸馏出**多个**技能，交付为 Anthropic 格式的技能文件夹。镜像 tag：
`localhost:5000/p_user1/algo-xskill:v1`。

## 定位

- **角色**：k8s Job 里的 **sidecar 容器**（`restartPolicy: Always`），与评测主容器
  共享 `/shared`。
- **职责**：起 xskill daemon、跑若干训练 rollout 产生轨迹、等 daemon 蒸馏毕业，把毕业的
  技能收集到 `/shared/skill/skills/<name>/SKILL.md`，落 `DONE`，然后 `sleep infinity`。
- **技能约定**：**多技能文件夹** `skills/<name>/SKILL.md`（Anthropic 格式）。评测镜像把
  它们拷进 `.claude/skills`、以 `XSKILL_SKILL_MODE=native` 让 agent 按需用 **Skill 工具**
  （区别于 skillopt/trace2skill 的单文件 reference 注入）。

## 算法原理（xskill）

xskill 是个 **daemon**：watch 一个 Claude Code `projects/` 目录里的 agent 轨迹
（`*.jsonl`），把轨迹切成 **atom**，由 cluster agent 给每个 atom 打 weightscore 并归入
某个候选技能；当某技能累计分数过 **毕业门槛** 时，`SkillEditAgent` 把候选整理成正文
`SKILL.md` 并 git "毕业"（`baby` 分支 → `main`）。所以训练 = "让 agent 真去解题产生轨迹
+ 给 daemon 时间入库/聚类/毕业"。

- LLM（生成/打分/cluster/SkillEdit）走 `deepseek-v4-flash`（OpenAI 兼容端点）。
- **embedding**（atom 入库 + 向量检索）DeepSeek 无此 API，改用 **DashScope**
  `text-embedding-v4` —— 故本镜像比另外两个**多需要一个 `DASHSCOPE_API_KEY`**。
- xskill 不读环境变量/key 文件，配置从 `config.yaml` 模板在运行时替换占位符后落到
  `$XSKILL_HOME/config.yaml`。

## reduced 规模的核心难点：毕业门槛

`baby→main` 毕业门槛是 `candidates.py` 里**硬编码**的 `ATOM_PROMOTION_THRESHOLD`
（默认 10）。runner 构造 `SkillEditAgent` 时**不传 threshold**，所以 `config.yaml` 的
`candidates.threshold` 改不动这个 v2 门槛。4 条轨迹 → 4 个 atom、单 atom 常只有 6–9 分
且分散在不同技能 → 攒不满 10 → 零毕业 → 全停在 `baby`（stub 占位符，不是真蒸馏内容）。

**对策**：入口在运行时用正则把 `candidates.py` 里的 `ATOM_PROMOTION_THRESHOLD` patch 成
`XSKILL_PROMO_THRESHOLD`（默认 **5**），让单条好轨迹就能让其技能毕业、产出真正蒸馏的正文。

## 训练流程（`entrypoint_train.sh`）

1. 用真实 key 替换 `config.yaml` 占位符，落到 `$XSKILL_HOME/config.yaml`。
2. **patch** `ATOM_PROMOTION_THRESHOLD` → `$PROMO_THRESHOLD` 并重新 import 校验。
3. seed claude home `settings.json`（`bypassPermissions` + skill 列举预算）。
4. 起 daemon（`HOME=$XHOME xskill serve --home $HOME_ROOT`，watch
   `$HOME_ROOT/.claude/projects`），带 `ensure_daemon` 看门狗。
5. **串行**跑训练样本（默认 **4** 题，烘焙在 `train_split/train`）：每题用 SkillOpt
   `eval_only.py` + `claude_code_exec`（`XSKILL_SKILL_MODE=native`、`IS_SANDBOX=1`，
   `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`）跑一个 rollout，轨迹 `*.jsonl`
   落到被 watch 的 `projects/`；每题后 `ITEM_SETTLE` 秒让 daemon 入库/聚类/可能毕业。
6. 全部样本后再 `FINAL_SETTLE` 秒收尾，`collect_skills` 三级回退收集：
   **A** main 分支毕业技能（首选）→ **B** 任意非 stub 正文 → **C** 兜底 baby stub（会
   在日志标注）。收集到 `/shared/skill/skills/<name>/`（连同 `references/scripts/assets/
   templates`），写 `ALGO=xskill`、`touch DONE`。零产出则不写 `DONE`。

> **`IS_SANDBOX=1` 为何关键**：容器以 root 跑，claude CLI 默认拒绝 root 下的
> `--dangerously-skip-permissions`；不设此变量则 rollout 的 claude 秒退、`projects/`
> 永远空、daemon 无轨迹可蒸馏 → 零技能。镜像 ENV 与 rollout 子壳里都设了它。

## 训练规模

**无 `TRAIN_SCALE` 开关**——4 题已烘焙进 `train_split/train`
（id：`32438 398-14 47766 48365`）。扩大规模需替换 `train_split` 并重建镜像；过门槛后
也可调高 `XSKILL_PROMO_THRESHOLD` 让毕业更严格。

## 打榜环境变量

提交时经 `env_text` 注入；两个 key 经 `K8S_JOB_SECRET` Secret 注入，勿明文写 `env_text`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | （必填，Secret 注入） | LLM（生成/打分/cluster/SkillEdit）key |
| `DASHSCOPE_API_KEY` | （必填，Secret 注入） | DashScope embedding key（**本镜像独有**） |
| `EVAL_MODEL` | `deepseek-v4-flash` | rollout target 模型名 |
| `XSKILL_PROMO_THRESHOLD` | `5` | patch 进 `candidates.py` 的毕业门槛 |
| `XSKILL_DAEMON_PORT` | `8791` | daemon 监听端口（127.0.0.1） |
| `ITEM_SETTLE` | `120` | 每题后等 daemon 入库/聚类/毕业的秒数 |
| `FINAL_SETTLE` | `150` | 全部样本后再等一轮聚类/毕业的秒数 |
| `ITEM_TIMEOUT` | `420` | 单 rollout `claude_code_exec` 超时秒数 |
| `TRAIN_SPLIT` | `/app/train_split` | 训练 split 目录 |
| `DATA_ROOT` | `/data` | SpreadsheetBench 工作簿根（烘焙的 26M） |
| `SKILL_DIR` | `/shared/skill` | 技能产出目录（产物落 `skills/` 子目录，勿改） |

## 构建

```bash
bash build.sh          # 从 ../../../vendor/{xskill,SkillOpt,data_root,sync_skills_to.sh} 暂存 _ctx 并 build+push
```

镜像装 Node 20 + `@anthropic-ai/claude-code`（rollout 用）。xskill 源码无 `.git`，故
Dockerfile 用 `SETUPTOOLS_SCM_PRETEND_VERSION` 兜底版本号。
