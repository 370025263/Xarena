# Xarena — CLAUDE.md

Guidance for AI agents (and humans) working in this repository. This file is the
canonical, comprehensive description of what Xarena is, how it is wired, how to
stand it up from scratch, and the non-obvious gotchas baked into the code.

> **Secrets policy (read first):** No API keys or secrets live in this repo. They
> are provided at **install time** (`./install.sh` prompts for them, writes a
> **gitignored** `config.local.env`, and creates a Kubernetes Secret
> `algo-secrets`). Never hardcode a key in any tracked file. The secret-scan in
> `install.sh`/CI treats a leaked key as a hard failure.

---

## 1. What this is

**Xarena** is a **Kubernetes-native leaderboard framework** plus a
**SpreadsheetBench × Skill evaluation harness**.

- **Platform** = a Flask backend + a Streamlit frontend that together implement a
  multi-role leaderboard: participants submit *algorithms* (as container images);
  a board creator/maintainer owns a board and its evaluator; an admin manages
  users. Submissions are run as **K8s Jobs** and scored, with per-task detail
  persisted and rendered in the UI.
- **Harness** = a concrete board ("Spreadsheet Skill Bench") that compares the
  *skills* produced by three skill-distillation algorithms — **SkillOpt**,
  **Trace2Skill**, **xskill** — on the **SpreadsheetBench** test split, scored in
  **single mode** (`claude_code_exec`) with the **deepseek-v4-flash** model on a
  **10×-reduced** test split (for a fast first result, not a full run).

The whole thing currently runs in a local **kind** (Kubernetes-in-Docker) single
-node cluster, with an in-cluster image **registry** on `localhost:5000`.

---

## 2. Architecture — the two-image, K8s-native pipeline

The framework was written for Kubernetes from the start. A submission becomes a
single K8s **Job** with **one Pod that has two containers** sharing an `emptyDir`
volume mounted at `/shared`:

```
submit(algorithm_image_url = a 打榜/algo image) on a board
  → backend  app.py:create_job_manifest → create_namespaced_job
      └─ K8s Job (1 Pod, native sidecar pattern):
           ├─ submitter-container  (algorithm; init/sidecar, restartPolicy: Always)
           │     TRAINS for real → writes a skill to /shared/skill/
           │       {skill.md  |  skills/<name>/SKILL.md} + ALGO + DONE
           │     then `sleep infinity`  (a sidecar must NOT exit, or it restarts/retrains)
           └─ evaluator-container  (the board's eval image; the MAIN container)
                 waits for /shared/skill/DONE
                 → reads the skill, runs eval_only.py on the SpreadsheetBench test split
                 → per task: input/output/task.md/chat-log/Yes-No
                 → POSTs per-task results + final score back to leaderboard-api-svc:80
  backend natively reconciles Job status (_sync_submission_status) and harvests
  Pod logs (_persist_submission_logs) on terminal state.
```

Key contract details (algorithm writes, evaluator reads — keep these in sync):

- **Shared signal dir** `SKILL_DIR=/shared/skill`. The algorithm writes its skill
  there plus a marker file `DONE` (and `ALGO` naming the algo). The evaluator
  blocks on `DONE`.
- **Single vs multi skill conventions** (the evaluator's `_resolve_skill`
  auto-detects):
  - **Single skill** — `skillopt` and `trace2skill` deliver one `skill.md`
    (injected into the system prompt via `--skill <file>`, "reference" mode).
  - **Multi skill** — `xskill` delivers a folder `skills/<name>/SKILL.md` in the
    Anthropic native Skills layout; evaluated with `XSKILL_SKILL_MODE=native`,
    materialized into an isolated `.claude/skills/`, with `--skill` pointing at an
    empty file.
- **Eval modes** (the board picks one via its evaluator image):
  - **single** = `target_backend = claude_code_exec` (the "with-harness" mode; the
    skill drives a Claude-Code harness whose model is deepseek-v4-flash). This is
    the headline comparison.
  - **multi** = direct chat (`openai_chat`).
  - **react** = a ReAct loop.
- **POST-back contract is golden**: the evaluator only **POSTs** `metrics` and
  per-task `failure`/detail; it never changes the backend schema. The backend
  persists into `Submission*` / `SubmissionEvalDetail` (with `extra_json` carrying
  `task_md`, `chatmessage_log`, `mode`, gold/pred, pass/fail).

### The six self-contained images

| Class | Image (registry `localhost:5000`) | What it bakes / does |
| --- | --- | --- |
| 打榜/algo | `p_user1/algo-skillopt:v1` | SkillOpt + 26M data + train split; really runs `train.py` (reduced: `num_epochs=1`, `train_size=6`) → `best_skill.md` → `skill.md` |
| 打榜/algo | `p_user1/algo-trace2skill:v1` | Trace2Skill; really runs the trajectory→evolution pipeline on ~6 tasks → `SKILL.md` → `skill.md` |
| 打榜/algo | `p_user1/algo-xskill:v1` | xskill + Claude Code + SkillOpt; runs `xskill serve` + claude_code_exec rollouts + distillation/promotion → a **multi-skill folder** |
| 榜单/eval | `l_creator/spreadsheet-eval-single:latest` | SkillOpt + Claude Code + data + test split; `evaluator.py` in **single** mode |
| 榜单/eval | `l_creator/spreadsheet-eval-multi:latest`  | same, **multi** mode |
| 榜单/eval | `l_creator/spreadsheet-eval-react:latest`  | same, **react** mode |

Plus the two **platform** images: `leaderboard-api:v2-k8s` (backend) and
`leaderboard-ui:v2-k8s` (frontend).

**Why self-contained?** The host venvs are Python 3.11 bound to `/usr/bin` and
cannot be reused across containers, so each image `pip install`s its own deps and
**bakes** its sources + the 26M SpreadsheetBench data; Pods mount no host volumes.
Images are loaded into the kind node with `kind load docker-image` and run with
`imagePullPolicy: Never`.

---

## 3. Directory map

```
.
├── install.sh                     ← CANONICAL from-scratch installer (interactive)
├── CLAUDE.md  README.md  readme   ← docs (readme = original Chinese task brief)
├── DEPLOYMENT_REPORT.md           ← delivery report (architecture + results)
├── config.local.env               ← (gitignored) keys, written by install.sh
│
├── backend/                       ← Flask backend (the leaderboard framework)
│   ├── app.py                     ← API, models, CLI (init-db), create_job_manifest
│   ├── agent/                     ← optional agent-LLM helpers (runner/tools)
│   ├── Dockerfile  requirements.txt
│   ├── namespace.yaml rbac.yaml priority_class.yaml
│   ├── beckend_deployment.k8s.yaml   ← backend Deployment+Service for kind (v2-k8s)
│   ├── frontend_deployment.k8s.yaml  ← frontend Deployment+Service (NodePort 30002)
│   └── beckend_deployment.yaml       ← original framework yaml (kept for reference)
│
├── frontend/                      ← Streamlit UI (app.py) + Dockerfile
│
├── leadeboard_apps/
│   └── spreadsheets_bench/
│       ├── readme.md              ← bench spec
│       ├── skillopt_eval/         ← the evaluator (single/multi/react)
│       │   ├── evaluator.py       ← waits for skill → eval_only.py → POST back
│       │   ├── Dockerfile  build.sh build_single|multi|react.sh
│       │   ├── bench_test_split/  ← baked 10×-reduced test split
│       │   └── run_submission.sh run_single_eval.sh post_results.py  ← host local-exec path
│       ├── algo_skillopt/  algo_trace2skill/  algo_xskill/   ← the 3 打榜 images
│       │   └── {Dockerfile, entrypoint_train.sh, build.sh, requirements.txt, *_split/}
│       ├── algo_common/down_wheels.sh   ← offline wheels helper (intranet legacy)
│       ├── baseline_algo/         ← the RAG demo template the algos were derived from
│       └── skills/                ← sample produced skills (single + xskill folder)
│
├── vendor/                        ← VENDORED third-party sources (ships, for offline build)
│   ├── SkillOpt/ Trace2Skill/ xskill/   ← code (+ LICENSE each)
│   ├── data_root/                 ← 26M SpreadsheetBench data (dereferenced)
│   ├── sync_skills_to.sh  README.md
│
├── run/                           ← helper scripts (kind-config, host launchers, pw_* acceptance)
└── registry/                      ← notes for the localhost:5000 registry
```

Note the directory is spelled `leadeboard_apps` (sic) and the backend file is
`beckend_deployment*.yaml` (sic) — kept as-is to avoid churn.

---

## 4. Install from scratch

Use the interactive installer at the repo root:

```bash
./install.sh          # step-by-step; each step prints what it does and asks to continue
./install.sh --help   # usage
```

It performs: **(0)** preflight (docker/kubectl/kind/node/curl; offers to fetch
kind), **(1)** prompt for `DEEPSEEK_API_KEY` (required) + `DASHSCOPE_API_KEY`
(optional) → gitignored `config.local.env` (chmod 600), **(2)** local
`registry:2` on `:5000`, **(3)** vendor check, **(4)** build all 8 images,
**(5)** create kind cluster `lb` with the port mappings, **(6)** `kind load` all
images, **(7)** apply namespace/rbac/priorityclass + create the `algo-secrets`
Secret + apply backend/frontend, **(8)** `flask init-db --create-defaults`,
**(9)** verify + print URLs and default credentials.

Default users created by `init-db --create-defaults`:

| Username | Password | Role |
| --- | --- | --- |
| `admin` | `adminpass` | admin |
| `l_creator` | `creatorpass` | creator / maintainer |
| `p_user1` | `user1pass` | participant |

Access after install: frontend on the host at `http://<host>:7799` (kind maps
NodePort 30002→7799), backend API at `http://<host>:30001`.

---

## 5. The role model

- **Participant** (`p_user1`) — submits an algorithm to a board by
  `algorithm_image_url` (a 打榜 image); sees own submissions + scores + per-task
  detail.
- **Creator / Maintainer** (`l_creator`) — owns a board, sets its `evaluator_image`
  and resource spec; manages the board.
- **Admin** (`admin`) — manages users and global state.

Roles drive the frontend navigation; the backend enforces them on the API.

---

## 6. The eval pipeline (Spreadsheet Skill Bench)

- **Single mode** (headline): `target_backend = claude_code_exec`,
  `target_model = deepseek-v4-flash`, `env.mode = single`, on the **10×-reduced**
  SpreadsheetBench test split (`bench_test_split/`, the baked
  `data/test_10pct_split`). The produced skill is injected (reference) or
  materialized (xskill native), and a Claude-Code harness solves each task; the
  evaluator diffs against the golden spreadsheet and records Yes/No per task.
- The evaluator (`skillopt_eval/evaluator.py`) was **rewritten from the original
  RAG evaluator** (`evaluator_rag_original.py.bak`, gitignored): it keeps the
  golden POST-back contract (`_post_metrics` / `_post_failure`, fields unchanged)
  and swaps the RAG QA/RAGAS body for: wait-for-skill → `eval_only.py` → parse
  per-task → POST.
- Per-task detail (`task.md`, chat log, solution, pass/fail) is persisted to
  `SubmissionEvalDetail.extra_json` and shown in the UI (rankings + Plotly radar +
  per-question correctness).

To run the **full** split instead of reduced, submit with `env_text` containing
`TRAIN_SCALE=full` (injected into the algo sidecar via `extra_env`).

---

## 7. How to add a new algorithm image

1. Create `leadeboard_apps/spreadsheets_bench/algo_<name>/` with a `Dockerfile`,
   an `entrypoint_train.sh`, `requirements.txt`, and a small `*_split/`.
2. The `entrypoint_train.sh` must: train for real, write the skill to
   `/shared/skill/` (`skill.md` for single, or `skills/<n>/SKILL.md` for multi),
   write `ALGO` + `touch DONE`, then **`sleep infinity`** (the sidecar has
   `restartPolicy: Always`; if it exits it will be restarted and retrain).
3. Add a `build.sh` that stages `_ctx/` from `../../../vendor/...` (repo-relative,
   offline) and `docker build`/`push` to `localhost:5000/p_user1/algo-<name>:v1`.
   Do **not** reference machine-specific host paths — copy from `vendor/`.
4. `kind load docker-image` it (install.sh step 6 / `run/build_images.sh`), then
   submit `algorithm_image_url=localhost:5000/p_user1/algo-<name>:v1`.

---

## 8. Key gotchas baked into the code (do not "fix" these blindly)

- **`IS_SANDBOX=1`** is baked into the eval and xskill images: a `root` user runs
  the `claude` CLI inside the container, and Claude Code refuses
  `--dangerously-skip-permissions` as root unless `IS_SANDBOX=1` is set (the
  container *is* the sandbox).
- **Absolute `claude` path**: under concurrent workers, spawning the bare name
  `claude` intermittently throws `FileNotFoundError: 'claude'`. Always resolve an
  absolute path (`command -v claude` / nvm path) and fail loudly if missing —
  never pass an empty path. See `run_single_eval.sh:resolve_claude` and the
  xskill entrypoint's `$(command -v xskill)`.
- **pandas pinned `<3`**: the eval/algo stacks assume pandas 2.x APIs; do not let
  pandas 3 in.
- **Algo sidecar must `sleep infinity`** after writing the skill — see above.
- **xskill has no `.git` when vendored** → its build uses
  `SETUPTOOLS_SCM_PRETEND_VERSION` so setuptools-scm can derive a version. The
  algo installs it editable from `vendor/xskill`.
- **nginx + kind port mapping**: kind maps NodePort `30002→host:7799` (frontend)
  and `30001→host:30001` (backend). A reverse proxy (e.g. nginx) can front the
  frontend on a domain and route `/api/` to the backend NodePort. Streamlit must
  run with `--server.enableCORS=false --server.enableXsrfProtection=false` behind
  a proxy or it white-screens.
- **Single-node resources**: the cluster is small, so evaluator container
  requests/limits are env-configurable (`K8S_EVAL_CPU_REQ/MEM_REQ/CPU_LIMIT/
  MEM_LIMIT`) and submissions are run serially to avoid OOM.
- **Docker Hub may be TLS-intercepted** in some environments; pull base images via
  the daocloud mirror (`docker.m.daocloud.io/...`) as a fallback (install.sh
  mentions this). pip uses the Tsinghua mirror.
- **Secret injection**: the backend gets `K8S_JOB_SECRET=algo-secrets`; when set,
  `create_job_manifest` adds `envFrom: secretRef(algo-secrets)` to both Job
  containers, delivering `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`. The agent-LLM
  env `AGENT_LLM_API_KEY` ships **empty** in the yaml and is injected at deploy
  time (or via secretKeyRef).

---

## 9. Keys & secrets — recap

- Provided at install time only; written to **gitignored** `config.local.env`
  (chmod 600) and surfaced to the cluster as the `algo-secrets` Secret.
- Job Pods receive them via `envFrom` (`K8S_JOB_SECRET=algo-secrets`).
- `vendor/` ships third-party source but **no keys**. The `*.bak` original
  evaluator and all `_ctx/`/`offline_wheels/` build artifacts are gitignored.
- If you ever add code that reads a key, read it from the environment /
  `config.local.env` — never commit a literal. The intranet proxy IPs (`50.67.x`)
  in `baseline_algo/` and `beckend_deployment.yaml` are documented legacy
  network references, **not** secrets, and are intentionally kept.
