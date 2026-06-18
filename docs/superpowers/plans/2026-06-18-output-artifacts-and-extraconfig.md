# Output-Artifact Persistence + Extraconfig Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every job's run artifacts to a durable `/models/xarena/<bench>/<jobid>/{eval,algo}/` dir, keep the evaluator's DB `extra_json` (extraconfig) for visualization, and expose extraconfig via a REST API + an agent tool — then prove it on a new fast mini board and push to GitHub.

**Architecture:** The Job already mounts a durable hostPath `/models` into BOTH containers. We inject one env var `OUTPUT_DIR=/models/xarena/<bench_slug>/<job_name>`; each container copies its own run output there at the end (evaluator → `$OUTPUT_DIR/eval`, algo → `$OUTPUT_DIR/algo`). The DB side is unchanged in shape (evaluator keeps POSTing per-item `extra_json`); we only add a read REST endpoint + an agent tool over the existing `submission_eval_details` table.

**Tech Stack:** Flask + SQLAlchemy (SQLite at `/db/leaderboard.db`), kubernetes-python client, kind cluster `lb` (ns `leaderboard`, registry `localhost:5000`, `imagePullPolicy: Never` → `kind load` + rollout/resubmit), Streamlit frontend, agno agent.

## Global Constraints

- **Minimal changes** — touch the fewest lines; do not refactor unrelated code.
- **NO phase coupling** — this is a *general* eval framework. Do NOT introduce `train/val/test` semantics, columns, or tabs. Only the generic container roles `eval` and `algo`.
- **No new DB table / no schema migration.** Reuse the existing `SubmissionEvalDetail.extra_json` (extraconfig). Durable files go to `/models/xarena`, NOT the DB.
- `/models` is already mounted at `/models` in both containers (hostPath `MODEL_HOST_PATH=/models`, or PVC `MODEL_PVC_NAME`). Durable across pod deletion (lives on the kind node). Do not add a new volume.
- Secrets: never hardcode/commit keys; job keys arrive via the `algo-secrets` Secret (`envFrom`). `OUTPUT_DIR` copies must never copy a key file.
- Images run `imagePullPolicy: Never`: after any image rebuild → `docker push localhost:5000/...` → `kind load docker-image <img> --name lb` → `kubectl rollout restart deploy/<name> -n leaderboard` (platform) or resubmit (job images).
- Git identity is preconfigured; push target is `git@github.com:370025263/Xarena.git`. Commit message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `backend/app.py` `create_job_manifest` (~2061) | inject `OUTPUT_DIR` env into both containers | modify |
| `backend/app.py` (new route near 2205) | `GET /api/submission/<id>/extra` REST read of extraconfig | add |
| `backend/app.py` `init_agent_routes(...)` call (~3938) | pass `SubmissionEvalDetail` to the agent layer | modify |
| `backend/agent/runner.py` `init_agent_routes` + AgentDeps build | thread `SubmissionEvalDetail` through; register new tool | modify |
| `backend/agent/tools.py` `AgentDeps` + new `@tool` | `read_submission_extra` over `submission_eval_details` | modify |
| `leadeboard_apps/spreadsheets_bench/skillopt_eval/evaluator.py` `main()` | copy eval run dir → `$OUTPUT_DIR/eval` | modify |
| `leadeboard_apps/spreadsheets_bench/algo_skillopt/entrypoint_train.sh` | copy `$RUN` → `$OUTPUT_DIR/algo` | modify |
| `leadeboard_apps/spreadsheets_bench/algo_trace2skill/entrypoint_train.sh` | copy `$RUN` → `$OUTPUT_DIR/algo` | modify |
| `leadeboard_apps/spreadsheets_bench/algo_xskill/entrypoint_train.sh` | copy skill-repo+logs+rollouts → `$OUTPUT_DIR/algo` | modify |
| `leadeboard_apps/spreadsheets_bench/data/test_mini5_split/` | new 5-task fast split | create |
| `leadeboard_apps/spreadsheets_bench/skillopt_eval/build_mini.sh` | build eval image with the 5-task split | create |

---

## Task 1: Framework — inject `OUTPUT_DIR` into the Job manifest

**Files:**
- Modify: `backend/app.py` `create_job_manifest` (~2061–2116)

**Interfaces:**
- Produces: env var `OUTPUT_DIR=/models/xarena/<bench_slug>/<job_name>` present in BOTH `evaluator-container` and `submitter-container`. `<bench_slug>` = slug of the board name (fallback `lb<id>`).

- [ ] **Step 1: Add bench slug + OUTPUT_DIR computation** at the top of `create_job_manifest` body (after the `models_mount`/`shared_mount` lines, ~2094). `re` and `Leaderboard` are already in module scope.

```python
    # 通用产物目录（不耦合 phase）：/models/xarena/<bench_slug>/<jobid>
    # 两容器都挂了 /models（hostPath，跨 pod 持久）；各自把输出拷到 $OUTPUT_DIR/{eval,algo}。
    _board = Leaderboard.query.get(leaderboard_id)
    _bench_slug = re.sub(r"[^a-z0-9]+", "-", ((_board.name if _board else "") or "").lower()).strip("-")[:40] or f"lb{leaderboard_id}"
    output_dir = f"/models/xarena/{_bench_slug}/{job_name}"
```

- [ ] **Step 2: Append OUTPUT_DIR to both base env lists.** In `base_eval_env` (after the `ALGO_API_ENDPOINT` line ~2106) and `base_algo_env` (after the `API_INTERNAL_URL` line ~2116), add:

```python
        client.V1EnvVar(name="OUTPUT_DIR", value=output_dir),
```

(One line in each list.)

- [ ] **Step 3: Confirm `re` is imported** at the top of `backend/app.py`.

Run: `grep -nE '^import re|^import os' backend/app.py | head`
Expected: a line `import re` exists. If missing, add `import re` near the other stdlib imports.

- [ ] **Step 4: Rebuild + reload + roll out the backend.**

```bash
cd /home/admin/leaderboard/backend
docker build --build-arg http_proxy= --build-arg https_proxy= --build-arg no_proxy=localhost,127.0.0.1 -t localhost:5000/leaderboard-api:v2-k8s .
docker push localhost:5000/leaderboard-api:v2-k8s
kind load docker-image localhost:5000/leaderboard-api:v2-k8s --name lb
kubectl rollout restart deploy/leaderboard-api -n leaderboard
kubectl rollout status deploy/leaderboard-api -n leaderboard --timeout=120s
```

Expected: `successfully rolled out`. **Note:** the backend serves submissions; this restart is fine because no eval Job is mid-POST (verify with `kubectl get jobs -n leaderboard` showing none `Running`, or wait for any to finish).

- [ ] **Step 5: Verify OUTPUT_DIR reaches a container.** (Done via the mini-board submit in Task 6 — defer the live assertion there.) For now confirm the manifest code path:

Run: `kubectl exec -n leaderboard deploy/leaderboard-api -- python -c "import app; print('OUTPUT_DIR' in open('/app/app.py').read())"`
Expected: `True`.

- [ ] **Step 6: Commit.**

```bash
cd /home/admin/leaderboard
git add backend/app.py
git commit -m "feat(framework): inject OUTPUT_DIR=/models/xarena/<bench>/<jobid> into job containers"
```

---

## Task 2: Evaluator — copy its run output to `$OUTPUT_DIR/eval`

**Files:**
- Modify: `leadeboard_apps/spreadsheets_bench/skillopt_eval/evaluator.py` `main()` (end, ~404–410)

**Interfaces:**
- Consumes: env `OUTPUT_DIR` (Task 1). Reads existing module global `OUT_DIR` (the eval run dir, `= /shared/eval_<sid>_<mode>`).
- Produces: `$OUTPUT_DIR/eval/` populated with the eval run (results.jsonl, predictions/, eval_summary.json). The existing `_post_metrics(...)` DB POST is UNCHANGED.

- [ ] **Step 1: Add a copy helper + call it at the end of `main()`** — right after `_post_metrics(metrics, eval_details=eval_details)` (~406), insert:

```python
    # 通用产物落盘：把本次评测 run 目录拷到 $OUTPUT_DIR/eval（不耦合 phase）。
    _out_base = os.environ.get("OUTPUT_DIR", "").strip()
    if _out_base:
        _dst = os.path.join(_out_base, "eval")
        try:
            os.makedirs(_dst, exist_ok=True)
            subprocess.run(["cp", "-a", out_dir + "/.", _dst], check=False)
            _log(f"copied eval artifacts -> {_dst}")
        except Exception as e:
            _log(f"WARN: copy eval artifacts failed: {e}")
```

(`subprocess` and `os` are already imported; `out_dir` is the local from `_run_eval`.)

- [ ] **Step 2: Rebuild the eval image(s).** The mini build (Task 6) will rebuild `single`; also rebuild the current single tag so the 28-task board benefits:

```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench/skillopt_eval
bash build.sh single
kind load docker-image localhost:5000/l_creator/spreadsheet-eval-single:latest --name lb
```

Expected: `built+pushed spreadsheet-eval-single`.

- [ ] **Step 3: Commit.**

```bash
cd /home/admin/leaderboard
git add leadeboard_apps/spreadsheets_bench/skillopt_eval/evaluator.py
git commit -m "feat(eval): copy eval run dir to \$OUTPUT_DIR/eval (durable artifacts)"
```

---

## Task 3: Algorithms — copy run output to `$OUTPUT_DIR/algo`

**Files:**
- Modify: `algo_skillopt/entrypoint_train.sh`, `algo_trace2skill/entrypoint_train.sh`, `algo_xskill/entrypoint_train.sh` (each, just before the final `sleep infinity`)

**Interfaces:**
- Consumes: env `OUTPUT_DIR` (Task 1).
- Produces: `$OUTPUT_DIR/algo/` populated with the algo run (skillopt/t2s: the `$RUN` out_root; xskill: skill repo + daemon log + rollout logs).

- [ ] **Step 1: skillopt** — in `algo_skillopt/entrypoint_train.sh`, replace the success tail (line ~17-18) so the copy happens before `sleep infinity`:

```bash
cp "$BEST" "$SKILL_OUT/skill.md"; echo skillopt > "$SKILL_OUT/ALGO"; touch "$SKILL_OUT/DONE"
echo "DONE: $SKILL_OUT/skill.md"
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR/algo"
  cp -a "$RUN/." "$OUTPUT_DIR/algo/" 2>/dev/null || true
  echo "copied algo artifacts -> $OUTPUT_DIR/algo"
fi
sleep infinity
```

- [ ] **Step 2: trace2skill** — in `algo_trace2skill/entrypoint_train.sh`, just before the final `sleep infinity` (line ~65), insert the same block (its out_root is also `$RUN`):

```bash
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR/algo"
  cp -a "$RUN/." "$OUTPUT_DIR/algo/" 2>/dev/null || true
  echo "copied algo artifacts -> $OUTPUT_DIR/algo"
fi
sleep infinity
```

- [ ] **Step 3: xskill** — in `algo_xskill/entrypoint_train.sh`, just before the final `sleep infinity` (line ~310), insert (xskill's artifacts are spread across the skill repo, the daemon log, and rollout logs):

```bash
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR/algo"
  [ -d "$XSKILL_SKILL_DIR" ] && cp -a "$XSKILL_SKILL_DIR" "$OUTPUT_DIR/algo/skill_repo" 2>/dev/null || true
  cp -a "$DAEMON_LOG" "$OUTPUT_DIR/algo/" 2>/dev/null || true
  cp -a /tmp/x_*.log "$OUTPUT_DIR/algo/" 2>/dev/null || true
  cp -a "$SKILLS_OUT" "$OUTPUT_DIR/algo/skills_shipped" 2>/dev/null || true
  echo "copied algo artifacts -> $OUTPUT_DIR/algo"
fi
sleep infinity
```

- [ ] **Step 4: Rebuild the 3 algo images.**

```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench
for d in algo_skillopt algo_trace2skill algo_xskill; do (cd "$d" && bash build.sh); done
for img in algo-skillopt algo-trace2skill algo-xskill; do kind load docker-image localhost:5000/p_user1/$img:v1 --name lb; done
```

Expected: three `built+pushed ...` lines + three `kind load` successes.

- [ ] **Step 5: Commit.**

```bash
cd /home/admin/leaderboard
git add leadeboard_apps/spreadsheets_bench/algo_*/entrypoint_train.sh
git commit -m "feat(algos): copy training run dir to \$OUTPUT_DIR/algo (skillopt/trace2skill/xskill)"
```

---

## Task 4: REST API — view a submission's extraconfig

**Files:**
- Add: `backend/app.py` new route after `_serve_internal_uploads` (~2208)

**Interfaces:**
- Produces: `GET /api/submission/<int:sub_id>/extra` → `{submission_id, count, items:[{question_id, is_correct, pred_answer, extra}]}` where `extra` = parsed `extra_json`. JWT-protected; visibility = owner or board owner or admin.

- [ ] **Step 1: Add the endpoint.** (Mirror the visibility check used by `get_submission_logs`.)

```python
@app.route('/api/submission/<int:sub_id>/extra', methods=['GET'])
@jwt_required()
def get_submission_extra(sub_id):
    """查看某次提交回传的 extraconfig（submission_eval_details.extra_json）。"""
    sub = Submission.query.get(sub_id)
    if not sub:
        return jsonify({"msg": "submission not found"}), 404
    uid = get_jwt_identity()
    me = User.query.get(uid)
    board = Leaderboard.query.get(sub.leaderboard_id)
    is_admin = bool(me and me.role == 'admin')
    is_owner = (sub.user_id == uid)
    is_board_owner = bool(board and board.owner_id == uid)
    if not (is_admin or is_owner or is_board_owner):
        return jsonify({"msg": "forbidden"}), 403
    qid = request.args.get('question_id')
    q = SubmissionEvalDetail.query.filter_by(submission_id=sub_id)
    if qid:
        q = q.filter_by(question_id=str(qid))
    rows = q.limit(int(request.args.get('limit', 200))).all()
    items = []
    for r in rows:
        try:
            extra = json.loads(r.extra_json) if r.extra_json else None
        except Exception:
            extra = None
        items.append({"question_id": r.question_id, "is_correct": r.is_correct,
                      "pred_answer": r.pred_answer, "extra": extra})
    return jsonify({"submission_id": sub_id, "count": len(items), "items": items})
```

- [ ] **Step 2: Rebuild + roll out backend** (same commands as Task 1 Step 4).

- [ ] **Step 3: Verify** against an existing submission with eval details (sub 5):

```bash
B=http://localhost:30001
T=$(curl -s -X POST $B/api/login -H 'Content-Type: application/json' -d '{"username":"p_user1","password":"user1pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s "$B/api/submission/5/extra?limit=2" -H "Authorization: Bearer $T" | python3 -m json.tool | head -25
```
Expected: JSON with `count` > 0 and `items[].extra` containing the per-item trajectory keys (e.g. `chatmessage_log`, `task_md`).

- [ ] **Step 4: Commit.**

```bash
cd /home/admin/leaderboard
git add backend/app.py
git commit -m "feat(api): GET /api/submission/<id>/extra — read extraconfig"
```

---

## Task 5: Agent tool — read a submission's extraconfig (logs already covered)

**Files:**
- Modify: `backend/agent/tools.py` (`AgentDeps` dataclass ~23 + new `@tool` + ensure it's exported)
- Modify: `backend/agent/runner.py` (`init_agent_routes` signature + `AgentDeps(...)`/`BackendRefs` build + the `agent_tools = [...]` list ~386)
- Modify: `backend/app.py` (`init_agent_routes(...)` call ~3938 — pass `SubmissionEvalDetail`)

**Interfaces:**
- Consumes: `AgentDeps.SubmissionEvalDetail` (the model class), `deps.flask_app`, `_visible_submission_query`, `_json_load_maybe` (existing helpers in tools.py).
- Produces: agent tool `read_submission_extra(submission_id: int, question_id: str | None = None, limit: int = 20)`. (Logs are already covered by the existing `get_task_logs(task_id)` — verify, don't re-add.)

- [ ] **Step 1: Add `SubmissionEvalDetail` field to `AgentDeps`** (in `backend/agent/tools.py`, in the dataclass near the other model fields like `Submission: Any`):

```python
    SubmissionEvalDetail: Any = None
```

- [ ] **Step 2: Add the tool** in `backend/agent/tools.py` (next to `get_task_logs`):

```python
@tool
def read_submission_extra(submission_id: int, question_id: str | None = None,
                          limit: int = 20, run_context: RunContext | None = None) -> str:
    """读取某次提交回传的 extraconfig（per-item extra_json：含轨迹/聊天日志等）。"""
    deps = _deps(run_context)
    with deps.flask_app.app_context():
        s = _visible_submission_query(deps).filter(deps.Submission.id == int(submission_id)).first()
        if not s:
            return json.dumps({"ok": False, "error": "submission not found"}, ensure_ascii=False)
        if deps.SubmissionEvalDetail is None:
            return json.dumps({"ok": False, "error": "eval-detail model not wired"}, ensure_ascii=False)
        q = deps.SubmissionEvalDetail.query.filter_by(submission_id=s.id)
        if question_id:
            q = q.filter_by(question_id=str(question_id))
        rows = q.limit(int(limit or 20)).all()
        out = [{"question_id": r.question_id, "is_correct": r.is_correct,
                "extra": _json_load_maybe(r.extra_json)} for r in rows]
        return json.dumps({"ok": True, "count": len(out), "items": out}, ensure_ascii=False)[:8000]
```

- [ ] **Step 3: Thread the model through `runner.py`.** In `backend/agent/runner.py`:
  - Add `SubmissionEvalDetail=None` param to `init_agent_routes(...)`.
  - Pass it into the `BackendRefs`/`AgentDeps` construction (wherever `Submission=...` is set).
  - Import the tool and add `read_submission_extra` to the `agent_tools = [ ... ]` list (~386).

Run (locate the exact spots): `grep -nE 'def init_agent_routes|Submission=|agent_tools *=|get_task_logs' backend/agent/runner.py`
Expected: shows the signature, the deps build, and the tools list to edit.

- [ ] **Step 4: Pass `SubmissionEvalDetail` from `app.py`.** In the `init_agent_routes(...)` call (~3938), add:

```python
    SubmissionEvalDetail=SubmissionEvalDetail,
```

- [ ] **Step 5: Rebuild + roll out backend** (Task 1 Step 4 commands).

- [ ] **Step 6: Verify** the agent can call it (and logs still work):

```bash
B=http://localhost:30001
T=$(curl -s -X POST $B/api/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"adminpass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
SID=$(curl -s "$B/api/agent/session" -H "Authorization: Bearer $T" | python3 -c 'import sys,json;print(json.load(sys.stdin)["agent_session_id"])')
curl -s -N -X POST "$B/api/agent/chat" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d "{\"agent_session_id\":\"$SID\",\"message\":\"用工具读取提交5的extraconfig，前2条，并说说看到了什么\"}" | grep -E 'tool_start|tool_end' | head
```
Expected: a `tool_start {"name":"read_submission_extra"}` then `tool_end`.

- [ ] **Step 7: Commit.**

```bash
cd /home/admin/leaderboard
git add backend/agent/tools.py backend/agent/runner.py backend/app.py
git commit -m "feat(agent): read_submission_extra tool + wire SubmissionEvalDetail into AgentDeps"
```

---

## Task 6: New fast mini board + end-to-end 验收

**Files:**
- Create: `leadeboard_apps/spreadsheets_bench/data/test_mini5_split/{train,val,test}/items.json`
- Create: `leadeboard_apps/spreadsheets_bench/skillopt_eval/build_mini.sh`

**Interfaces:**
- Consumes: all prior tasks (OUTPUT_DIR injection, eval/algo copy, REST, agent tool).
- Produces: board "Spreadsheet Mini (5-task, fast)" with eval image `localhost:5000/l_creator/spreadsheet-eval-single:mini5`; one submitted run whose artifacts land in `/models/xarena/<bench>/<jobid>/{eval,algo}`.

- [ ] **Step 1: Build a 5-task test split** (subset of the existing baked 28-task test split; train/val tiny too for speed):

```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench
python3 - <<'PY'
import json, os, shutil
src="skillopt_eval/bench_test_split"; dst="data/test_mini5_split"
shutil.rmtree(dst, ignore_errors=True)
for split, n in [("train",3), ("val",2), ("test",5)]:
    os.makedirs(f"{dst}/{split}", exist_ok=True)
    items=json.load(open(f"{src}/{split}/items.json"))
    json.dump(items[:n], open(f"{dst}/{split}/items.json","w"), ensure_ascii=False)
    print(split, "->", min(n,len(items)))
PY
```
Expected: `train -> 3`, `val -> 2`, `test -> 5`.

- [ ] **Step 2: Add `build_mini.sh`** that bakes the 5-task split into a `:mini5` eval image. Create `skillopt_eval/build_mini.sh`:

```bash
#!/usr/bin/env bash
# 榜单镜像 · mini 快速验收（5 题 test）。复用 single 模式 evaluator，只换 baked split。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"
if [ ! -d _ctx/SkillOpt ] || [ ! -d _ctx/data_root ]; then
  rm -rf _ctx && mkdir _ctx
  cp -r "$VENDOR/SkillOpt"  _ctx/SkillOpt
  cp -r "$VENDOR/data_root" _ctx/data_root
fi
# 用 mini5 split 覆盖构建上下文里的 bench_test_split
rm -rf _ctx_mini && cp -r . _ctx_mini 2>/dev/null || true
docker build --build-arg TARGET_MODE=single \
  --build-context minisplit=../data/test_mini5_split \
  -t "$REG/l_creator/spreadsheet-eval-single:mini5" . \
  -f Dockerfile.mini
docker push "$REG/l_creator/spreadsheet-eval-single:mini5"
echo "built+pushed spreadsheet-eval-single:mini5"
```

**Simpler approach (preferred — avoids buildx contexts):** instead of the above, temporarily swap the baked split and reuse the existing Dockerfile:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"; VENDOR="${VENDOR:-../../../vendor}"
[ -d _ctx/SkillOpt ] || { rm -rf _ctx && mkdir _ctx; cp -r "$VENDOR/SkillOpt" _ctx/SkillOpt; cp -r "$VENDOR/data_root" _ctx/data_root; }
rm -rf bench_test_split.bak && mv bench_test_split bench_test_split.bak
cp -r ../data/test_mini5_split bench_test_split
trap 'rm -rf bench_test_split && mv bench_test_split.bak bench_test_split' EXIT
docker build --build-arg TARGET_MODE=single -t "$REG/l_creator/spreadsheet-eval-single:mini5" .
docker push "$REG/l_creator/spreadsheet-eval-single:mini5"
echo "built+pushed spreadsheet-eval-single:mini5"
```
Use the simpler approach. Then:

```bash
cd /home/admin/leaderboard/leadeboard_apps/spreadsheets_bench/skillopt_eval
bash build_mini.sh
kind load docker-image localhost:5000/l_creator/spreadsheet-eval-single:mini5 --name lb
```
Expected: `built+pushed spreadsheet-eval-single:mini5` + kind load OK; `bench_test_split` restored to 28 tasks (trap).

- [ ] **Step 3: Create the mini board** via API as `l_creator`:

```bash
B=http://localhost:30001
T=$(curl -s -X POST $B/api/login -H 'Content-Type: application/json' -d '{"username":"l_creator","password":"creatorpass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -X POST $B/api/leaderboards -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{
  "name":"Spreadsheet Mini (5-task, fast)",
  "description":"5 题快速验收板：single+flash，验证 OUTPUT_DIR 产物 + extraconfig。",
  "evaluator_image":"localhost:5000/l_creator/spreadsheet-eval-single:mini5",
  "version":"mini5",
  "resource_spec":"{\"requests\":{\"cpu\":\"1\",\"memory\":\"2Gi\"},\"limits\":{\"cpu\":\"3\",\"memory\":\"6Gi\"}}"
}' | python3 -m json.tool
```
Expected: JSON with a new board `id` (note it as `MINI_BOARD_ID`).

- [ ] **Step 4: Submit skillopt to the mini board** as `p_user1`:

```bash
B=http://localhost:30001
PT=$(curl -s -X POST $B/api/login -H 'Content-Type: application/json' -d '{"username":"p_user1","password":"user1pass"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -X POST $B/api/leaderboard/<MINI_BOARD_ID>/submit -H "Authorization: Bearer $PT" -H 'Content-Type: application/json' \
  -d '{"submission_name":"skillopt-mini5-verify","algorithm_image_url":"localhost:5000/p_user1/algo-skillopt:v1","env_text":"","params":{}}' | python3 -m json.tool
```
Expected: `status: Pending`, a `submission_id` (note as `MINI_SID`) and `job_name` (note as `MINI_JOB`).

- [ ] **Step 5: Wait for terminal, then assert artifacts + extraconfig.** Poll `my-submissions` until `MINI_SID` is `Succeeded`/`Failed`. Then:

```bash
# (a) durable artifacts on the node
docker exec lb-control-plane sh -lc 'find /models/xarena -maxdepth 4 -type d | head; echo "--- eval files ---"; find /models/xarena -path "*/eval/*" | head; echo "--- algo files ---"; find /models/xarena -path "*/algo/*" | head'
# (b) extraconfig REST
curl -s "$B/api/submission/<MINI_SID>/extra?limit=2" -H "Authorization: Bearer $PT" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("extra count",d["count"]); print("keys",list((d["items"][0]["extra"] or {}).keys())[:8] if d["count"] else "none")'
```
Expected: (a) `/models/xarena/spreadsheet-mini-5-task-fast/<job>/eval/...` and `.../algo/...` both contain files; (b) `extra count > 0` with trajectory keys.

- [ ] **Step 6: Commit the mini-board assets.**

```bash
cd /home/admin/leaderboard
git add leadeboard_apps/spreadsheets_bench/data/test_mini5_split leadeboard_apps/spreadsheets_bench/skillopt_eval/build_mini.sh
git commit -m "feat(bench): mini 5-task fast board (split + build_mini.sh) for 验收"
```

---

## Task 7: Final 验收 + push to GitHub

**Files:** none (verification + release)

- [ ] **Step 1: Full green check** — confirm in one place: mini board scored; `/models/xarena/<bench>/<job>/{eval,algo}` populated; `GET /api/submission/<MINI_SID>/extra` returns data; agent `read_submission_extra` fires; existing 28-task board still works (`GET /api/leaderboard/1/rankings`).

- [ ] **Step 2: Secret-scan the diff** before pushing.

```bash
cd /home/admin/leaderboard
git diff origin/main --stat
git diff origin/main | grep -inE 'sk-[a-z0-9]{20,}|sk-ant-|AKIA[0-9A-Z]{16}|-----BEGIN' && echo "!!! SECRET — STOP" || echo "secret-scan clean"
```
Expected: `secret-scan clean`.

- [ ] **Step 3: Push to GitHub.**

```bash
git push origin HEAD
git log --oneline -8
```
Expected: push succeeds to `github.com:370025263/Xarena`.

---

## Self-Review

- **Spec coverage:** OUTPUT_DIR `/models/xarena/<bench>/<jobid>` ✓(T1); eval→`/output/eval` & algo→`/output/algo` copy at end ✓(T2,T3); extraconfig stays in DB for viz ✓(unchanged, T2 keeps `_post_metrics`); agent tool views logs (existing `get_task_logs`) + extraconfig ✓(T5); REST API for extraconfig ✓(T4); NO phase coupling ✓(only `eval`/`algo`); minimal changes ✓(env+copy+1 endpoint+1 tool); new fast mini board + submit + 验收 ✓(T6); push to GitHub ✓(T7).
- **Placeholder scan:** all steps carry concrete code/commands; `<MINI_BOARD_ID>/<MINI_SID>/<MINI_JOB>` are runtime values noted at capture time (acceptable — they're outputs, not undefined code refs).
- **Type consistency:** `OUTPUT_DIR` env name, `$OUTPUT_DIR/{eval,algo}` layout, `read_submission_extra(submission_id, question_id, limit)`, and `GET /api/submission/<id>/extra` shape are consistent across T1–T6.
