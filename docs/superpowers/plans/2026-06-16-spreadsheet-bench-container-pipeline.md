# SpreadsheetBench × Skill —— K8s 原生容器化评测管线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `leadeboard_apps/spreadsheets_bench/readme.md` 用**框架原生的 K8s 路径**跑评测：本机起 kind 单节点集群，把整套 leaderboard（backend+frontend 入集群）+ 6 个自包含镜像（3 打榜算法真训练 + 3 榜单评测）部署进去，提交 → 后端**原生** `create_namespaced_job`（算法=init/sidecar 容器训练产 skill→`/shared`，评测=main 容器读 skill 评测回传）。不再用我手写的 docker-run/LOCAL_EXECUTOR 旁路。

**Architecture:** 框架本就是为 K8s 写的（`create_job_manifest`：submitter-container[算法 init/sidecar] + evaluator-container[评测 main] 共享 `emptyDir /shared`；从 Job status 同步、从 Pod 读日志）。走原生 = 用现成调试过的代码，避免我平行重写引 bug。镜像**自包含**（bake 源码+依赖+26M 数据+split），`kind load docker-image` 灌入节点，`imagePullPolicy: Never`，Pod 不挂宿主卷。算法 sidecar 训练完写 `/shared/skill/{skill.md|skills/*}`+`DONE` 后 `sleep infinity`（sidecar `restartPolicy:Always` 不能退出否则重启）；评测 main 等 `DONE`→`eval_only.py`(single=claude_code_exec / multi/react=openai_chat)→解析逐题→POST `leaderboard-api-svc:80`。

**Tech Stack:** kind(k8s-in-docker，隔离/易清，契合本机多容器现状) · kubectl/kubeadm(在) · python:3.11-slim 自包含镜像 · Node20+claude-code(single harness) · SkillOpt/Trace2Skill/xskill 训练源码 · deepseek-v4-flash(经 k8s Secret) · 本机 registry :5000 · nginx(patentdagger-nginx-1)→algo.xskill.wiki。

**为什么是 kind 不是 k3s/kubeadm：** 本机已跑大量 docker 容器 + 他人实验；kind 跑在 docker 里、不抢宿主 iptables/systemd、`kind delete cluster` 即净，最不打扰现状；自包含镜像 `kind load` 直灌、免 registry 拉取与 hostPath 数据挂载。

**Scope decision（默认，可改）：** 打榜训练默认**缩减规模真训练**（skillopt `num_epochs=1 train_size=6`；t2s/xskill 各 4-6 题），每镜像 8-20min 产真 skill；`TRAIN_SCALE=full` 旋钮跑全量。**真训练，非复用既有产物。** 提交串行（单 4cpu/15G 节点，避免并发 OOM）。

**最小框架改动（仅 2 处 config 级，非重写）：**
1. `create_job_manifest` evaluator 资源 `requests cpu8/mem90Gi` 单节点排不上 → 改为 env 可配小默认（`K8S_EVAL_CPU/MEM_REQ/LIMIT`）。
2. job pod 注入 deepseek key：当 `K8S_JOB_SECRET` 设置时给两容器加 `envFrom secretRef`。
（算法 sidecar 训练完 `sleep infinity` 由算法 entrypoint 解决，**不改框架**。）

---

## File Structure

```
leadeboard_apps/spreadsheets_bench/
  skillopt_eval/        evaluator.py(已改) Dockerfile(自包含) build_single|multi|react.sh requirements.eval.txt bench_test_split/
  algo_common/          down_wheels.sh(base→python:3.11-slim)
  algo_skillopt/        Dockerfile entrypoint_train.sh build.sh requirements.txt train_split/
  algo_trace2skill/     Dockerfile entrypoint_train.sh build.sh requirements.txt t2s_train/
  algo_xskill/          Dockerfile entrypoint_train.sh build.sh config.yaml train_split/
backend/
  app.py                改 create_job_manifest(评测资源 env 可配 + envFrom secret)；保留原 K8s 路径
  log_collector.py      实现最小流式采集 或 从 backend Deployment 去掉该 sidecar 容器
  beckend_deployment.k8s.yaml   由 beckend_deployment.yaml 改：去 log-collector/或修；env 加 K8S_EVAL_*/K8S_JOB_SECRET；frontend svc
  frontend_deployment.k8s.yaml  frontend Deployment + NodePort 30002
  k8s_secret.yaml       algo-secrets(DEEPSEEK_API_KEY/DASHSCOPE_API_KEY)
run/
  kind-config.yaml      kind 集群(extraPortMappings 30001→host, 30002→7799)
  k8s_up.sh             一键：起 kind→load 镜像→apply→init-db→建用户
```

镜像（registry/节点）：
```
localhost:5000/leaderboard-api:v2-k8s        backend(含 create_job_manifest 改动)
localhost:5000/leaderboard-ui:v2-k8s         frontend
l_creator/spreadsheet-eval-single|multi|react:dsv4flash-10pct   榜单 ×3
p_user1/algo-skillopt|trace2skill|xskill:v1                     打榜 ×3
```

---

## Task 1: 自包含榜单镜像(评测) ×1(single) + ×2(multi/react)

**Files:** `skillopt_eval/{evaluator.py(改默认),requirements.eval.txt,Dockerfile,build_single|multi|react.sh,bench_test_split/}`

- [ ] **Step 1: requirements.eval.txt**
```
requests>=2.30.0
openai>=1.30.0
pyyaml>=6.0
numpy>=1.24.0
openpyxl>=3.1.0
pandas>=2.0.0
httpx>=0.27.0
tiktoken>=0.7.0
azure-identity>=1.15.0
azure-core>=1.30.0
tenacity>=8.0.0
```

- [ ] **Step 2: evaluator.py 默认 env 指向 bake 路径（自包含）**
Modify in `evaluator.py` 环境配置段：
```python
REPRO_ROOT = getenv_nonempty("REPRO_ROOT", "/app")
SKILLOPT_DIR = getenv_nonempty("SKILLOPT_DIR", "/app/SkillOpt")
EVAL_PY = getenv_nonempty("EVAL_PY", "/usr/local/bin/python")
DATA_ROOT = getenv_nonempty("DATA_ROOT", "/data")
```

- [ ] **Step 3: Dockerfile（bake SkillOpt+deps+claude+data+test split）**
```dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn CLAUDE_CODE_EXEC_USE_SDK=cli
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git bash && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code && claude --version || true
COPY requirements.eval.txt /app/requirements.eval.txt
RUN pip install --no-cache-dir -r /app/requirements.eval.txt
COPY _ctx/SkillOpt /app/SkillOpt
RUN pip install --no-cache-dir -e /app/SkillOpt || true
ENV PYTHONPATH=/app/SkillOpt
COPY _ctx/data_root /data
WORKDIR /app
COPY evaluator.py /app/evaluator.py
COPY bench_test_split/ /bench/data/test_10pct_split/
ARG TARGET_MODE=single
ENV TARGET_MODE=${TARGET_MODE} SKILLOPT_DIR=/app/SkillOpt EVAL_PY=/usr/local/bin/python DATA_ROOT=/data \
    SPLIT_DIR=/bench/data/test_10pct_split SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash WORKERS=3
CMD ["python","/app/evaluator.py"]
```

- [ ] **Step 4: 准备 bake 上下文**
```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench/skillopt_eval
rm -rf _ctx && mkdir _ctx
cp -r /home/admin/research/reproduce_speatsheeds/sources/SkillOpt _ctx/SkillOpt
cp -r "$(cat /home/admin/research/reproduce_speatsheeds/repro/data/DATA_ROOT.txt)" _ctx/data_root
ls _ctx/SkillOpt/scripts/eval_only.py && ls _ctx/data_root | head
```
Expected: 打印 eval_only.py 路径 + `dataset.json spreadsheet`。

- [ ] **Step 5: 构建三模式**（build_single/multi/react.sh 已存在；改 tag 前缀与上节一致）
```bash
bash build_single.sh && bash build_multi.sh && bash build_react.sh
curl -s localhost:5000/v2/_catalog
```
Expected: catalog 含 `l_creator/spreadsheet-eval-single|multi|react`。

- [ ] **Step 6: 验证依赖+等 skill 逻辑**
```bash
docker run --rm localhost:5000/l_creator/spreadsheet-eval-single:latest python -c "import skillopt,openpyxl,pandas;print('eval deps OK')"
docker run --rm -e SUBMISSION_ID=0 -e SKILL_WAIT_TIMEOUT=6 -e API_INTERNAL_URL=http://localhost:1 localhost:5000/l_creator/spreadsheet-eval-single:latest 2>&1 | grep -E "Waiting for|超时" | head -2
```
Expected: `eval deps OK`；`Waiting for 打榜算法 skill 包...` 后超时。

- [ ] **Step 7: Checkpoint** — 三评测镜像在 registry，single 跑得起、会等 skill。

## Task 2: 打榜镜像·skillopt（真训练→单 skill.md，sidecar 训完 sleep）

**Files:** `algo_skillopt/{Dockerfile,entrypoint_train.sh,build.sh,requirements.txt,train_split/}`, `algo_common/down_wheels.sh`

- [ ] **Step 1: 缩减 train/val split bake**
```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench
mkdir -p algo_skillopt/train_split/{train,val,test}
python3 - <<'PY'
import json
s='/home/admin/research/reproduce_speatsheeds/repro/data/spreadsheetbench_split';o='algo_skillopt/train_split'
json.dump(json.load(open(f'{s}/train/items.json'))[:6],open(f'{o}/train/items.json','w'),ensure_ascii=False)
json.dump(json.load(open(f'{s}/val/items.json'))[:3],open(f'{o}/val/items.json','w'),ensure_ascii=False)
json.dump([],open(f'{o}/test/items.json','w'));print('train6 val3')
PY
```
Expected: `train6 val3`。

- [ ] **Step 2: requirements.txt** — 同 Task1 Step1（openai/openpyxl/pandas/tiktoken/...）。

- [ ] **Step 3: entrypoint_train.sh（真 train.py，缩减默认，full 旋钮，训完 sleep）**
```bash
#!/usr/bin/env bash
set -uo pipefail
SKILL_OUT="${SKILL_DIR:-/shared/skill}"; mkdir -p "$SKILL_OUT"
KEY="${DEEPSEEK_API_KEY:?}"; ENDPOINT="${TARGET_ENDPOINT:-https://api.deepseek.com}"; MODEL="${EVAL_MODEL:-deepseek-v4-flash}"
RUN=/tmp/run; rm -rf "$RUN"
if [ "${TRAIN_SCALE:-reduced}" = "full" ]; then SPLIT=/app/full_split; EOPT=""; TS=80; else SPLIT=/app/train_split; EOPT="train.num_epochs=1"; TS=6; fi
cd /app/SkillOpt
python scripts/train.py --config configs/spreadsheetbench/default.yaml \
  --optimizer_backend openai_chat --target_backend openai_chat \
  --optimizer_model "$MODEL" --target_model "$MODEL" \
  --optimizer_azure_openai_endpoint "$ENDPOINT" --optimizer_azure_openai_api_key "$KEY" --optimizer_azure_openai_auth_mode openai_compatible \
  --target_azure_openai_endpoint "$ENDPOINT" --target_azure_openai_api_key "$KEY" --target_azure_openai_auth_mode openai_compatible \
  --split_dir "$SPLIT" --data_root /data --seed 42 --workers "${WORKERS:-4}" --out_root "$RUN" \
  --cfg-options env.mode=multi train.train_size=$TS train.batch_size=3 gradient.minibatch_size=3 gradient.merge_batch_size=3 $EOPT
BEST=$(find "$RUN" -name best_skill.md | head -1)
[ -s "$BEST" ] || { echo "FATAL: 无 best_skill.md"; sleep infinity; }
cp "$BEST" "$SKILL_OUT/skill.md"; echo skillopt > "$SKILL_OUT/ALGO"; touch "$SKILL_OUT/DONE"
echo "DONE: $SKILL_OUT/skill.md"; sleep infinity   # sidecar(restartPolicy:Always) 训完不能退出，由评测 main 驱动 Pod 完成
```

- [ ] **Step 4: Dockerfile**
```dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
RUN apt-get update && apt-get install -y --no-install-recommends git bash && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY _ctx/SkillOpt /app/SkillOpt
RUN pip install --no-cache-dir -e /app/SkillOpt || true
ENV PYTHONPATH=/app/SkillOpt
COPY _ctx/data_root /data
WORKDIR /app
COPY train_split/ /app/train_split/
COPY entrypoint_train.sh /app/entrypoint_train.sh
RUN chmod +x /app/entrypoint_train.sh
ENV SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash WORKERS=4 TRAIN_SCALE=reduced
CMD ["bash","/app/entrypoint_train.sh"]
```

- [ ] **Step 5: algo_common/down_wheels.sh（尊重离线遗产，base 改 python:3.11-slim）** — 同前一版计划内容（BASE_IMAGE 默认 python:3.11-slim，proxy 可空，pip download → offline_wheels）。

- [ ] **Step 6: build.sh + 构建**
```bash
#!/usr/bin/env bash
set -euo pipefail
REG=localhost:5000; rm -rf _ctx && mkdir _ctx
cp -r /home/admin/research/reproduce_speatsheeds/sources/SkillOpt _ctx/SkillOpt
cp -r "$(cat /home/admin/research/reproduce_speatsheeds/repro/data/DATA_ROOT.txt)" _ctx/data_root
docker build -t "$REG/p_user1/algo-skillopt:v1" .
docker push "$REG/p_user1/algo-skillopt:v1"; echo built+pushed
```
Run it; Expected: built+pushed。

- [ ] **Step 7: 独立(非 k8s)冒烟：共享卷真训练产 skill**
```bash
SH=/tmp/sh_skillopt; rm -rf "$SH"; mkdir -p "$SH"
timeout 1800 docker run --rm --network host -v "$SH:/shared" -e DEEPSEEK_API_KEY="$(grep -oP 'DEEPSEEK_API_KEY=\K\S+' ~/.aikey)" \
  --entrypoint bash localhost:5000/p_user1/algo-skillopt:v1 -c 'bash /app/entrypoint_train.sh & p=$!; while [ ! -f /shared/skill/DONE ]; do sleep 5; done; echo SKILL_READY; kill $p' 2>&1 | tail -15
ls -la "$SH/skill"
```
Expected: `SKILL_READY` + `$SH/skill/skill.md`+`DONE`（真训练产物，非复用）。

- [ ] **Step 8: Checkpoint** — registry 含 algo-skillopt；冒烟产真 skill。

## Task 3: 打榜镜像·trace2skill（真训练→单 skill.md）
（结构同 Task2；entrypoint 跑 t2s 三步管线 run_spreadsheetbench→run_error_analysis→skill_evolver 产 SKILL.md→/shared/skill/skill.md+DONE→sleep infinity。Dockerfile bake `_ctx/Trace2Skill`(rsync 排除 data/.git) + data_root。**Step1 先读 `run_train_trace2skill.sh` 与 `t2s_train80` 布局，据实裁 6 题 + 填准三步参数/endpoint**。build/smoke 同 Task2。）

## Task 4: 打榜镜像·xskill（真训练→多 skill 文件夹）
（最重：镜像内自带 claude+xskill daemon；entrypoint 起 `xskill serve`→逐 train item `eval_only.py --target_backend claude_code_exec ... XSKILL_SKILL_MODE=native`→settle 蒸馏→`sync_skills_to.sh` 晋升→收 promoted `.claude/skills/*`→`/shared/skill/skills/`+DONE→sleep infinity。**Step1 先读 `run_xskill/phase3b_lib.sh`+`sync_skills_to.sh`+`.claude_test/settings.json` 作蓝本**。Dockerfile bake xskill wheel + SkillOpt(rollout) + sync 脚本 + data。smoke 同 Task2 但需注入 `DASHSCOPE_API_KEY`。）

> Task3/4 的 entrypoint 与裁数逻辑依赖 Step1 探明真实脚本/数据布局——这是"先探后写"的真实步骤，执行时据实落地，保持缩减 4-6 题与 `sleep infinity` 收尾。

## Task 5: 最小框架改动（评测资源 env 可配 + job secret 注入）

**Files:** Modify `backend/app.py` `create_job_manifest`

- [ ] **Step 1: evaluator 资源改 env 可配小默认**
把 `evaluator_container` 的 `resources=...` 改为：
```python
def _q(env, d): return os.environ.get(env, d)
evaluator_container = client.V1Container(
    name="evaluator-container", image=evaluator_image,
    image_pull_policy=os.environ.get("K8S_IMAGE_PULL_POLICY", "Never"),
    resources=client.V1ResourceRequirements(
        requests={"cpu": _q("K8S_EVAL_CPU_REQ","1"), "memory": _q("K8S_EVAL_MEM_REQ","2Gi")},
        limits={"cpu": _q("K8S_EVAL_CPU_LIMIT","3"), "memory": _q("K8S_EVAL_MEM_LIMIT","6Gi")}),
    env=base_eval_env + extra_env_vars + eval_extra_env,
    volume_mounts=[models_mount, shared_mount],
)
```

- [ ] **Step 2: job secret 注入（两容器 envFrom，仅当 K8S_JOB_SECRET 设置）**
在两容器构造后、PodSpec 之前：
```python
_job_secret = os.environ.get("K8S_JOB_SECRET")
if _job_secret:
    _ef = [client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name=_job_secret, optional=True))]
    evaluator_container.env_from = _ef
    if algorithm_container is not None:
        algorithm_container.env_from = _ef
```

- [ ] **Step 3: 语法校验**
Run: `cd /home/admin/leaderboard && .venv-backend/bin/python -c "import sys;sys.path.insert(0,'backend');import app;print('app import OK')"`（env: DATABASE_URL/UPLOADS_HOST_PATH）
Expected: `app import OK`。

- [ ] **Step 4: log_collector.py** — 评测镜像/算法日志已可由 `_persist_submission_logs`(终态读 Pod 日志) 覆盖，因此 backend Deployment **去掉 log-collector sidecar 容器**（避免我那个 no-op stub crash-loop）。在 `beckend_deployment.k8s.yaml`(Task6) 不含该容器。

- [ ] **Step 5: Checkpoint** — app import OK；两处改动 grep 命中。

## Task 6: 部署 K8s（kind 集群 + 框架原生 apply + backend/frontend 入集群）

**Files:** `run/kind-config.yaml`, `backend/k8s_secret.yaml`, `backend/beckend_deployment.k8s.yaml`, `backend/frontend_deployment.k8s.yaml`, `run/k8s_up.sh`

- [ ] **Step 1: 装 kind + 拉 node 镜像(走 mirror)**
```bash
ARCH=$(uname -m|sed 's/x86_64/amd64/;s/aarch64/arm64/')
sudo curl -fsSL -o /usr/local/bin/kind "https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-$ARCH" && sudo chmod +x /usr/local/bin/kind
docker pull docker.m.daocloud.io/kindest/node:v1.31.0 && docker tag docker.m.daocloud.io/kindest/node:v1.31.0 kindest/node:v1.31.0
kind version
```
Expected: kind 版本打印；kindest/node 本地存在。

- [ ] **Step 2: kind 集群配置（端口映射：backend 30001→host, frontend 30002→host:7799）**
Create `run/kind-config.yaml`:
```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - {containerPort: 30001, hostPort: 30001, protocol: TCP}
  - {containerPort: 30002, hostPort: 7799, protocol: TCP}
```

- [ ] **Step 3: 释放 host:7799（停旧 docker 前端，由 kind 接管）+ 起集群**
```bash
docker rm -f leaderboard-ui 2>/dev/null || true
kind create cluster --name lb --image kindest/node:v1.31.0 --config /home/admin/leaderboard/run/kind-config.yaml
kubectl get nodes
```
Expected: 节点 `lb-control-plane` Ready。

- [ ] **Step 4: 重建 backend/frontend 镜像（backend 含 Task5 改动）+ kind load 全部 8 镜像**
```bash
cd /home/admin/leaderboard/backend && docker build -t localhost:5000/leaderboard-api:v2-k8s . && docker push localhost:5000/leaderboard-api:v2-k8s
cd /home/admin/leaderboard/frontend && docker build -t localhost:5000/leaderboard-ui:v2-k8s . && docker push localhost:5000/leaderboard-ui:v2-k8s
for img in leaderboard-api:v2-k8s leaderboard-ui:v2-k8s \
  l_creator/spreadsheet-eval-single:latest l_creator/spreadsheet-eval-multi:latest l_creator/spreadsheet-eval-react:latest \
  p_user1/algo-skillopt:v1 p_user1/algo-trace2skill:v1 p_user1/algo-xskill:v1; do
  kind load docker-image --name lb localhost:5000/$img
done
docker exec lb-control-plane crictl images | grep -E "leaderboard|algo|eval" | head
```
Expected: 8 镜像在节点 containerd 内。

- [ ] **Step 5: Secret（deepseek/dashscope）**
Create `backend/k8s_secret.yaml`(用 stringData，apply 时由脚本注入真值避免明文入库) —— 实际用命令创建：
```bash
kubectl -n leaderboard create secret generic algo-secrets \
  --from-literal=DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  --from-literal=DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY" || true
# keys 来自部署时的环境（install.sh 从 config.local.env 读取，提示用户输入），
# 切勿在仓库中硬编码 <your-dashscope-key>。
```
（namespace 见 Step6 先 apply。）

- [ ] **Step 6: backend Deployment(改) + Service + frontend Deployment + NodePort**
Create `backend/beckend_deployment.k8s.yaml`：基于 `beckend_deployment.yaml`，改：① 单容器(去 log-collector)；② `image: localhost:5000/leaderboard-api:v2-k8s` `imagePullPolicy: Never`；③ env 增 `K8S_IMAGE_PULL_POLICY=Never`、`K8S_JOB_SECRET=algo-secrets`、`K8S_EVAL_CPU_REQ=1 K8S_EVAL_MEM_REQ=2Gi K8S_EVAL_CPU_LIMIT=3 K8S_EVAL_MEM_LIMIT=6Gi`、`API_INTERNAL_URL=http://leaderboard-api-svc:80`；④ Service 不变(NodePort 30001)。
Create `backend/frontend_deployment.k8s.yaml`：frontend Deployment(`localhost:5000/leaderboard-ui:v2-k8s`,Never)，env `API_BASE_URL=http://leaderboard-api-svc:80` `PUBLIC_BASE_URL=https://algo.xskill.wiki` + streamlit flags(enableCORS/Xsrf false)；Service NodePort 30002→8501。
Apply:
```bash
cd /home/admin/leaderboard/backend
kubectl apply -f namespace.yaml -f rbac.yaml -f priority_class.yaml
# 回到 Step5 建 secret(此时 namespace 已在)
kubectl apply -f beckend_deployment.k8s.yaml -f frontend_deployment.k8s.yaml
kubectl -n leaderboard rollout status deploy/leaderboard-api --timeout=180s
kubectl -n leaderboard rollout status deploy/leaderboard-ui --timeout=180s
```
Expected: 两 Deployment Ready。

- [ ] **Step 7: init-db + 建用户（exec 进 backend pod，框架原生 CLI）**
```bash
POD=$(kubectl -n leaderboard get pod -l app=leaderboard-api -o jsonpath='{.items[0].metadata.name}')
kubectl -n leaderboard exec "$POD" -- flask --app app.py init-db --create-defaults
```
Expected: `Created default admin/creator/participant`。

- [ ] **Step 8: 连通性**
```bash
curl -s -m5 -o /dev/null -w "backend NodePort:%{http_code}\n" -X POST http://localhost:30001/api/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"adminpass"}'
curl -s -m5 -o /dev/null -w "frontend NodePort(host7799):%{http_code}\n" http://localhost:7799/_stcore/health
```
Expected: backend 200；frontend 200。

- [ ] **Step 9: nginx /api/ 指向 backend NodePort(30001)，前端域名照旧**
把 `algo.xskill.wiki` 的 `location /api/` upstream 从 `7789` 改 `30001`(in-place 保 inode)，reload nginx。Verify `https://algo.xskill.wiki/_stcore/health`=200。

- [ ] **Step 10: Checkpoint** — `kubectl -n leaderboard get deploy,svc,pod` 全 Ready；域名可登录渲染。

## Task 7: 原生提交三算法 → K8s Job → 容器管线出最终成绩

- [ ] **Step 1: 建/改榜单 evaluator_image=single 评测镜像**
```bash
B=http://localhost:30001
CT=$(curl -s -X POST $B/api/login -d '{"username":"l_creator","password":"creatorpass"}' -H 'Content-Type: application/json'|python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -X POST $B/api/leaderboards -H "Authorization: Bearer $CT" -H 'Content-Type: application/json' -d '{"name":"Spreadsheet Skill Bench (single+flash, k8s)","version":"v2-k8s-10pct","evaluator_image":"localhost:5000/l_creator/spreadsheet-eval-single:latest","resource_spec":{"limits":{"cpu":"3","memory":"6Gi"},"requests":{"cpu":"1","memory":"2Gi"}},"sota_score":0}'
```
Expected: 返回 board id（记为 BID）。`resource_spec` 即算法 sidecar 资源(小，能排上)。

- [ ] **Step 2: 三算法各提交一次（algorithm_image_url=打榜镜像）**
```bash
PT=$(curl -s -X POST $B/api/login -d '{"username":"p_user1","password":"user1pass"}' -H 'Content-Type: application/json'|python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
for a in skillopt trace2skill xskill; do
  curl -s -X POST $B/api/leaderboard/$BID/submit -H "Authorization: Bearer $PT" -H 'Content-Type: application/json' \
    -d "{\"submission_name\":\"$a-k8s\",\"algorithm_image_url\":\"localhost:5000/p_user1/algo-$a:v1\",\"env_text\":\"\",\"params\":{}}"; echo " $a submitted"
done
```
Expected: 三 submission_id；后端**原生** `create_namespaced_job` 起 Job。

- [ ] **Step 3: 看 K8s Job/Pod 原生跑（init 算法→main 评测）**
```bash
kubectl -n leaderboard get jobs,pods
POD=$(kubectl -n leaderboard get pod -l app=leaderboard-eval -o jsonpath='{.items[0].metadata.name}' | head -1)
kubectl -n leaderboard logs "$POD" -c submitter-container --tail=20   # 算法真训练日志
kubectl -n leaderboard logs "$POD" -c evaluator-container --tail=20    # 评测日志
```
Expected: submitter 容器有 train.py 真训练输出；evaluator 容器 `Waiting for skill...`→评测→`POST metrics OK`。

- [ ] **Step 4: 串行等三条 Job 完成 + 看分数**
```bash
while :; do
  curl -s "$B/api/my-submissions?per_page=50" -H "Authorization: Bearer $(curl -s -X POST $B/api/login -d '{"username":"p_user1","password":"user1pass"}' -H 'Content-Type: application/json'|python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')" \
   | python3 -c 'import sys,json;d=json.load(sys.stdin);r=[(i["name"],i["status"],i["score"]) for i in d["items"]];[print(*x) for x in r];sys.exit(0 if r and all(x[1] in ("Succeeded","Failed") for x in r) else 1)' && break
  sleep 30
done
```
Expected: 三条 Succeeded，分数来自 K8s 容器管线。

- [ ] **Step 5: Checkpoint** — `kubectl get jobs` 三 Complete；分数非来自宿主旁路（local-exec 已不在路径上）。

## Task 8: 验收（Playwright）+ 报告 + 留痕

- [ ] **Step 1: Playwright 看成绩+逐题详情**（指向 https://algo.xskill.wiki；pw_final.py 已有）
Run: `cd /home/admin/leaderboard && API=http://localhost:30001 python3.11 run/pw_final.py 2>&1 | tail -15`
Expected: rankings 含三 `*-k8s`；逐题分析含 PASS/FAIL+task.md+聊天日志。

- [ ] **Step 2: 验证逐题 extra 来自容器评测**
```bash
POD=$(kubectl -n leaderboard get pod -l app=leaderboard-api -o jsonpath='{.items[0].metadata.name}')
kubectl -n leaderboard exec "$POD" -- python -c "
import app,json
with app.app.app_context():
    d=app.SubmissionEvalDetail.query.first();ex=json.loads(d.extra_json or '{}')
    print('task_md',bool(ex.get('task_md')),'chat',bool(ex.get('chatmessage_log')),'mode',ex.get('mode'))"
```
Expected: `task_md True chat True mode single`。

- [ ] **Step 3: down_wheels 留痕**
```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench/algo_skillopt
cp ../algo_common/down_wheels.sh . && bash down_wheels.sh 2>&1 | tail -3 && ls offline_wheels | head
```
Expected: offline_wheels 出现 .whl。

- [ ] **Step 4: 更新 DEPLOYMENT_REPORT.md** — 改为「kind 单节点 + 框架原生 Job：算法 init/sidecar 真训练→/shared→评测 main→回传」，附 K8s 管线最终成绩表 + `kubectl get jobs` 截图 + down_wheels 留痕。

- [ ] **Step 5: Final checkpoint** — `kind get clusters`=lb；`kubectl -n leaderboard get jobs` 三 Complete；域名前端三 `*-k8s` 成绩可见；报告更新。

---

## Self-Review

**Spec 覆盖：** 两类镜像(Task1 榜单×3 / Task2-4 打榜×3)✅；三模式 build_*.sh✅；single=claude_code_exec✅；单/多 skill 约定(evaluator `_resolve_skill`)✅；打榜 train/val 产 skill 发信号→榜单读 skill 包(init/sidecar+`/shared`+DONE，**框架原生**)✅；copy baseline_algo 引入三算法 train✅；train/val 入打榜镜像不含 test✅；down_wheels+代理保留✅；evaluator 改 skillopt 管线、只 POST 不改字段、可追溯(fail_reason/task_md/chatmessage_log)✅；逐题 input/output/task.md/聊天日志/Yes-No✅。

**K8s 原生 vs 旁路：** 删除上一版的 `LOCAL_EXECUTOR`/`orchestrate_container_run.sh`/`_local_exec_worker` 改动——不再需要；用框架 `create_namespaced_job`/init-container/`_persist_submission_logs`/`_sync_submission_status` 原生路径。仅保留 2 处 config 级微调(评测资源 env 可配 + job secret envFrom)。

**Placeholder：** Task3/4 entrypoint 明确标注"先读真实脚本(`run_train_trace2skill.sh`/`phase3b_lib.sh`)再据实落地"——是真实"先探后写"步骤，非 TBD。其余均完整可执行。

**契约一致性：** `/shared/skill/{skill.md|skills/*/SKILL.md,DONE,ALGO}` 算法写/评测读一致；`SKILL_DIR=/shared/skill`、`SUBMISSION_ID`、`API_INTERNAL_URL=http://leaderboard-api-svc:80`(框架默认) 贯穿一致；镜像 tag `p_user1/algo-*`/`l_creator/spreadsheet-eval-*` 在 build/load/submit 三处一致；sidecar `restartPolicy:Always`↔算法 `sleep infinity` 收尾匹配(否则重启重训)。

**已知风险（执行盯）：** ① 单 4cpu/15G 节点——评测资源已调小 + 串行提交，仍需盯 OOM；② kind pod egress 到 deepseek 需节点联网(kind 默认有)；③ xskill rollout 在 pod 内 spawn claude——用绝对路径避免 `FileNotFoundError:'claude'`；④ kindest/node 走 daocloud mirror(docker hub 本机被 MITM)；⑤ nginx `/api/` 改指 30001 后域名日志流才通。
