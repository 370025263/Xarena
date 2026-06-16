# 打榜镜像 · skillopt

SpreadsheetBench × Skill 打榜算法镜像之一。**真训练**一个单文件技能 `skill.md`，
交付给榜单评测镜像。镜像 tag：`localhost:5000/p_user1/algo-skillopt:v1`。

## 定位

- **角色**：k8s Job 里的 **sidecar 容器**（`restartPolicy: Always`），与评测主容器
  共享 `emptyDir` 卷 `/shared`。
- **职责**：跑 SkillOpt 的 `scripts/train.py`，把训练出的最佳技能写到
  `/shared/skill/skill.md`，落 `DONE` 标记，然后 `sleep infinity`（sidecar 退出会被
  k8s 当作崩溃无限重启，故任何路径末尾都不退出）。
- **技能约定**：**单文件 `skill.md`**。评测镜像以 *reference 模式* 把它注入到
  system prompt（区别于 xskill 的多技能文件夹 + native Skill 工具）。

## 算法原理（SkillOpt）

SkillOpt 把"技能"当作可优化的文本对象，用一个 **optimizer LLM** 在 SpreadsheetBench
训练样本上做"前向评测 → 反向产出梯度文本 → 合并改写技能"的迭代：

1. 用当前技能让 **target LLM** 解一批训练题（`env.mode=multi`，codegen 直出）。
2. 对失败样本，optimizer LLM 产出"文本梯度"（应该补什么、改什么）。
3. 按 `minibatch`/`merge_batch` 把梯度合并回技能，得到下一轮候选。
4. 取整个流程里评测最好的候选 `best_skill.md` 交付。

optimizer 与 target 都走 **openai_chat** 后端、同一个 `deepseek-v4-flash`、同一个
OpenAI 兼容端点（`--*_auth_mode openai_compatible`）。

## 训练流程（`entrypoint_train.sh`）

| 变量来源 | 值 |
| --- | --- |
| split | reduced=`/app/train_split`（train 6 题 / val 3 题）；full=`/app/full_split` |
| 数据 | `/data`（烘焙进镜像的 26M SpreadsheetBench 工作簿） |
| 关键 cfg | `env.mode=multi train.train_size=6 train.batch_size=3 gradient.minibatch_size=3 gradient.merge_batch_size=3 train.num_epochs=1`（reduced） |

产出：`find $RUN -name best_skill.md` → `cp` 到 `$SKILL_DIR/skill.md`，写 `ALGO=skillopt`、
`touch DONE`。若没产出 `best_skill.md` 则不写 `DONE`、直接 `sleep infinity`（让评测侧
等待超时而非误用空技能）。

## 训练规模：reduced vs full

本镜像是**唯一**带 `TRAIN_SCALE` 开关的算法镜像：

- `TRAIN_SCALE=reduced`（默认）：train_size=6、1 epoch，分钟级跑通、出初步结果。
- `TRAIN_SCALE=full`：切到 `/app/full_split`、train_size=80、去掉 `num_epochs=1` 限制。
  ⚠️ `full_split` 当前**未烘焙进镜像**，全量需先把 80 题的 split 放进
  `train_split/`→`full_split/` 并重建镜像（见 `build.sh`）。

## 打榜环境变量

提交时通过打榜接口的 `env_text`（每行 `KEY=VALUE`）注入；密钥类经 k8s Secret
（`K8S_JOB_SECRET` → `envFrom`）注入，不要明文写进 `env_text`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TRAIN_SCALE` | `reduced` | `reduced`/`full`，见上节 |
| `WORKERS` | `4` | 训练并发（`train.py --workers`） |
| `EVAL_MODEL` | `deepseek-v4-flash` | optimizer + target 模型名 |
| `TARGET_ENDPOINT` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `DEEPSEEK_API_KEY` | （必填，Secret 注入） | LLM key；缺失则入口直接报错退出训练逻辑 |
| `SKILL_DIR` | `/shared/skill` | 技能产出目录（与评测容器约定，勿改） |

## 构建

```bash
bash build.sh          # 从 ../../../vendor/{SkillOpt,data_root} 暂存 _ctx 并 build+push
```

`_ctx/` 是构建上下文（`vendor/` 暂存物，已 gitignore）；`down_wheels.sh` 为离线
环境预下载 `requirements.txt` 的 wheel（联网环境用不到）。
