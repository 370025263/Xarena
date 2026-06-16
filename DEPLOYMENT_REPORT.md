# Leaderboard 部署 + SpreadsheetBench×Skill 榜单 —— 交付报告（K8s 原生容器管线）

> 任务来源：`/home/admin/leaderboard/readme` + `leadeboard_apps/spreadsheets_bench/*/readme.md`
> 访问：**https://algo.xskill.wiki**（推荐，HTTPS 域名） · 直连 NodePort `http://<host>:7799`
> 实施计划：`docs/superpowers/plans/2026-06-16-spreadsheet-bench-container-pipeline.md`

## 0. 一句话

按 readme 的**两类镜像 + K8s 原生**架构落地：本机 kind 单节点集群，整套 leaderboard（backend/frontend）入集群；提交走**框架原生** `create_namespaced_job` —— **算法 init/sidecar 容器真训练**产 skill 写共享卷 `/shared`，**评测 main 容器**读 skill 跑单模式 `claude_code_exec`+`deepseek-v4-flash`，逐题回传。**不再有我手写的宿主机编排旁路。**

## 1. 架构（框架原生，无旁路）

```
提交(algorithm_image_url=打榜镜像) → 后端 _start_k8s_job → create_namespaced_job
  └─ K8s Job (1 Pod, 原生 sidecar):
       ├─ submitter-container (init/sidecar, restartPolicy:Always) = 打榜镜像
       │     真训练 → 写 /shared/skill/{skill.md | skills/*/SKILL.md} + DONE → sleep infinity
       └─ evaluator-container (main) = 榜单镜像
             等 /shared/skill/DONE → eval_only.py(single, claude_code_exec, dsv4flash, 28题)
             → 逐题 input/output/task.md/聊天日志/Yes-No → POST leaderboard-api-svc:80
  后端 _sync_submission_status(Job状态) + _persist_submission_logs(Pod日志) 原生回收
```

- 共享 `emptyDir /shared` = readme 的"训练完发信号→榜单请求 skill 包"。算法 sidecar 训完 `sleep infinity`（`restartPolicy:Always` 退出会被重启重训），由评测 main 驱动 Pod 完成。
- 单/多 skill 两约定：skillopt/trace2skill 交付单 `skill.md`；xskill 交付多 skill 文件夹（native，Skill 工具）。evaluator `_resolve_skill` 自动识别。

## 2. 六个自包含镜像（registry localhost:5000，kind load 入节点）

| 类 | 镜像 | 内容 / 训练 |
| --- | --- | --- |
| 打榜 | `p_user1/algo-skillopt:v1` | bake SkillOpt+26M数据+train_split；**真跑 train.py**（缩减 `num_epochs=1 train_size=6`）→ best_skill.md |
| 打榜 | `p_user1/algo-trace2skill:v1` | bake Trace2Skill；**真跑 6 步管线**（轨迹→评测→错误分析→进化，6题）→ SKILL.md |
| 打榜 | `p_user1/algo-xskill:v1` | bake xskill+claude+SkillOpt；**真跑 daemon+4 次 claude_code_exec rollout+蒸馏晋升**→ 多 skill 文件夹 |
| 榜单 | `l_creator/spreadsheet-eval-single:latest` | bake SkillOpt+claude+数据+test split；`evaluator.py` 单模式 `claude_code_exec` |
| 榜单 | `l_creator/spreadsheet-eval-multi:latest` | 同上，multi（direct chat）模式 |
| 榜单 | `l_creator/spreadsheet-eval-react:latest` | 同上，react 模式 |

- 自包含原因：宿主 venv 是 py3.11 绑 `/usr/bin` 不可跨容器复用 → 镜像各自 `pip install`，Pod 不挂宿主卷。
- `evaluator.py` 由 RAG 版**改写**（非弃用）：保留黄金回传契约 `_post_metrics/_post_failure`（只 POST 不改字段，后端落库），把 algo /qa+RAGAS 换成 等 skill→eval_only.py→逐题解析。原 RAG 版备份 `evaluator_rag_original.py.bak`。
- `IS_SANDBOX=1` 烘焙进评测/xskill 镜像：容器内 root 下 claude 拒绝 `--dangerously-skip-permissions`，需此旗标。
- `down_wheels.sh`（内网离线轮子遗产）保留并验证可用（algo_skillopt 实测产出 39 个 wheel，内网可切 `--no-index --find-links offline_wheels`）。

## 3. 最小框架改动（仅 2 处 config 级，非重写）

`backend/app.py` `create_job_manifest`：
1. 评测容器资源由硬编码 `requests cpu8/mem90Gi`（单节点排不上）→ env 可配小默认（`K8S_EVAL_CPU/MEM_REQ/LIMIT`，默认 1cpu/2Gi req、3cpu/6Gi limit）。
2. `K8S_JOB_SECRET` 设置时给两容器加 `envFrom secretRef`（把 `algo-secrets` 的 deepseek/dashscope key 注入 Job Pod）。

其余框架代码、字段结构、K8s 路径、内网代理串全部原样保留。

## 4. K8s 容器管线最终成绩（缩减规模真训练，单模式+dsv4flash，28 题）

| 算法 | 分数 | pass | 约定 | 备注 |
| --- | --- | --- | --- | --- |
| **xskill** | **75.0%** | 21/28 | 多 skill 文件夹(native) | 1 个 promoted `openpyxl-cell-manipulation`，覆盖偏多的 cell-level 题 |
| **skillopt** | **35.71%** | 10/28 | 单 skill.md | 1 epoch/6 题，优化器在小集上 reject 改进 → 弱 skill |
| **trace2skill** | **28.57%** | 8/28 | 单 skill.md | 6 题进化，提升有限 |

> 这是**缩减规模真训练**（"先跑通有一个初步结果"）在容器里现产 skill 的评测值，逐题 84 行明细（task.md+聊天日志+solution+Yes/No）落库可追溯。与早先"宿主机用全量训练好 skill"那组（skillopt/t2s ~89%、xskill ~60%）不同且更噪属正常——6 题训练不足以让 skillopt/t2s 产出强 skill。要可比即把各打榜镜像 `TRAIN_SCALE=full` 打开重训。

## 5. 部署组件（全部 kind 集群内）

```
kind cluster 'lb' (单节点, k8s v1.31)
  ns leaderboard:
    Deployment leaderboard-api  (localhost:5000/leaderboard-api:v2-k8s, sa=leaderboard-api-sa)  Svc NodePort 30001
    Deployment leaderboard-ui   (localhost:5000/leaderboard-ui:v2-k8s, streamlit CORS/XSRF off) Svc NodePort 30002→host:7799
    rbac(Job/Pod/log 权限) · priorityclass · secret algo-secrets(deepseek/dashscope)
  kind extraPortMappings: 30001→host:30001, 30002→host:7799
nginx(patentdagger-nginx-1) algo.xskill.wiki: / → host:7799(前端) ; /api/ → host:30001(后端)
```
默认账号：`admin/adminpass`(admin) · `l_creator/creatorpass`(creator/maintainer) · `p_user1/user1pass`(participant)。

## 6. 验收（Playwright，眼见为实，截图于 run/shots/）

- 三角色登录 + 分权导航 ✓
- 榜单 `Spreadsheet Skill Bench (single+flash, k8s)`：实时排名（xskill/skillopt/trace2skill 三条容器管线成绩）+ Plotly 雷达图 + 逐题正确率分析 ✓
- 逐题详情（gold/pred/通过 + task.md/聊天日志）来自容器评测 ✓

## 7. 复跑 / 全量

- 复跑：登录 participant → 提交 `algorithm_image_url=localhost:5000/p_user1/algo-<algo>:v1` 到榜单 → 后端原生起 Job。
- 全量训练：提交时 `env_text` 加 `TRAIN_SCALE=full`（经 extra_env 注入算法 sidecar）。
- 看 Job：`kubectl -n leaderboard get jobs,pods`；看两容器日志：`kubectl -n leaderboard logs <pod> -c submitter-container|-c evaluator-container`。
