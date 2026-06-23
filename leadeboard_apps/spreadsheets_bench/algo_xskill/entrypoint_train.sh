#!/usr/bin/env bash
# ============================================================================
# 打榜镜像·xskill —— TEAM-CS ONLINE-EVOLUTION 训练入口 (epoch0 cold_flush 冷启动
#                     + epoch1..N team-CS 多用户灰度在线进化)。
#
# ── 训练范式（两段式）─────────────────────────────────────────────────────
# epoch0  = 冷启动：一个 daemon 既是 HTTP team server 又是 server_mode
#           DirectoryWatcher。本机直接用 multi_turn_rollout 写轨迹到 server 的
#           被 watch home（$HOME_ROOT/.claude/projects），跑完落 $BARRIER_FILE，
#           daemon 用 cold_flush=1 把每个有候选的 baby skill 批量毕业 baby->main
#           出 v1（绕开小数据下永远到不了的 weightscore 阈值）。
# epoch1..N = team-CS 灰度在线进化（核心）：起 N 个 worker，每个独立
#           HOME=/root/whome_$k（独立 .claude/projects+skills+settings.json），
#           cg_exec 隔离，作为 distinct `xskill connect --label worker$k` client
#           连本机 server。每个 worker 用 multi_turn_rollout（--chome 指各自
#           home/.claude）对分到的题解题——解题轨迹落各自 $WHOME/.claude/projects，
#           connect client 的 ingester 镜像成 traj_*.md 并上传 server。
#           server 端对每条上传轨迹做 CS 归因（score_atom 给 atom 按 side 打
#           ux_score）：epoch1 起 main 攒到 ux_score 后，正常在线 SkillEdit 把
#           "已有 main 技能的更新"路由到 commit_to_staging（开灰度分支）；后续
#           上传按 pick_side_scoped 分流 main/staging，check_and_decide 在样本
#           够时 staging_avg>=main_avg 则 promote v->v+1，否则 discard。
#
#           **关键决策**：epoch1..N **不落屏障**（cold_start.epochs=1 只让 epoch0
#           走 cold_flush）；epoch1 起 cold_flush=False，SkillEdit 走正常在线
#           增量路径 -> commit_to_staging 产生灰度对象 -> canary 决策真跑起来。
#
# ── 已确认的源码事实（file:line 由调研核实）──────────────────────────────
#  * serve --server: cli.py cmd_serve -> serve(server_mode=True)，一进程 =
#    FastAPI team server + server_mode watcher。
#  * join token: $XSKILL_HOME/team_server.json 的 **join_token** 字段
#    （team/server/state.py ensure_join_token；不是 .token）。
#  * connect --label: 成 client 指纹一部分，distinct label 避免同 hostname 塌缩
#    成一个 client_id（cli.py cmd_connect + client_registry.py）。
#  * client run_forever: collect_and_upload($HOME/.claude/projects 经 ingester
#    镜像成 ~/.xskill/cc_sessions/traj_*.md)->sync->reconcile_skill_sides(按
#    manifest 的 side 装 main/staging 到 $HOME/.claude/skills)。
#  * collector 去抖 quiet_seconds=180 / min_change_interval=600 **硬编码**在
#    TeamClient.__init__（connect 无 flag、不读 config）-> 训练里 runtime patch
#    调低（同 ATOM_PROMOTION_THRESHOLD 既有 patch 套路）。
#  * staging 前置：SkillEditAgent 守门 3——main 上开 staging 要求 main 已有真实
#    ux_score（_main_has_ux_score）。epoch1 worker 必须真"用到"已毕业技能（native
#    模式全量挂载），上传轨迹经 CS 归因给 main 打分后，下一轮 SkillEdit 才会开
#    staging。故每个在线 epoch 后留足 CANARY_SETTLE 让多轮 watcher tick 跑完。
#
# sidecar restartPolicy:Always —— 任何情况下都不退出（退出会无限重训），所有
# 路径（成功 / fatal）末尾都 sleep infinity。
# ============================================================================
set -uo pipefail

# ── 必需 key（运行时注入；xskill 不读环境变量，需写进 config.yaml）──────────
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY required}"
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:?DASHSCOPE_API_KEY required}"

# ── 产出目录 ────────────────────────────────────────────────────────────
SKILL_OUT="${SKILL_DIR:-/shared/skill}"
SKILLS_OUT="$SKILL_OUT/skills"          # Anthropic skills/ 多 skill 目录
mkdir -p "$SKILLS_OUT"

# ── 可调参数 ────────────────────────────────────────────────────────────
PROMO_THRESHOLD="${XSKILL_PROMO_THRESHOLD:-5}"   # patch 进 candidates.py 的毕业门槛
DAEMON_PORT="${XSKILL_DAEMON_PORT:-8791}"
FINAL_SETTLE="${FINAL_SETTLE:-150}"              # 每个 epoch 跑完后等一轮入库/聚类
ITEM_TIMEOUT="${ITEM_TIMEOUT:-420}"              # 单 rollout exec 超时
TRAIN_SPLIT="${TRAIN_SPLIT:-/app/train_split}"
DATA_ROOT="${DATA_ROOT:-/data}"
EVAL_MODEL="${EVAL_MODEL:-deepseek-v4-flash}"

# ── 并行 worker + 多轮 + 多 epoch 参数 ──────────────────────────────────
# WORKERS        : team-CS 在线 epoch 的并行 worker 数（= distinct client 数）
# MAX_TURNS      : 每题多轮纠错的最大轮数（透传 multi_turn_rollout.py --max-turns）
# EPOCHS         : 训练 epoch 数（epoch0=冷启动落屏障批量毕业；1..N-1=team-CS 灰度）
# CANARY_SETTLE  : 每个在线 epoch 后等待秒数，让"上传->蒸馏/打分->canary 决策"闭环跑完
# WORKER_CPU_MAX : cgroup v2 cpu.max 值（"<quota> <period>"，默认 80000 100000 = 0.8 核）
# WORKER_MEM_MAX : cgroup v2 memory.max 值（默认 2G）
WORKERS="${XSKILL_WORKERS:-3}"
MAX_TURNS="${XSKILL_MAX_TURNS:-5}"
EPOCHS="${XSKILL_EPOCHS:-4}"
CANARY_SETTLE="${XSKILL_CANARY_SETTLE:-600}"
WORKER_CPU_MAX="${XSKILL_WORKER_CPU_MAX:-80000 100000}"
WORKER_MEM_MAX="${XSKILL_WORKER_MEM_MAX:-2G}"

# ── team client 去抖 patch 值（runtime patch TeamClient/__init__ 默认）──────
# connect 不读 config、无 flag，故把 collector 去抖硬编码默认直接 patch 调低，
# 否则训练里 worker 每题轨迹要静默 600s 才上传 -> 单 epoch 等不起。
CLIENT_QUIET="${XSKILL_CLIENT_QUIET:-20}"          # mtime 静默窗口（默认 20s）
CLIENT_MIN_CHANGE="${XSKILL_CLIENT_MIN_CHANGE:-30}" # hash 去抖窗口（默认 30s）
CLIENT_POLL="${XSKILL_CLIENT_POLL:-10}"            # client run_forever poll_interval
# JsonlIngester 入库完成屏障（settle barrier）默认 120s——源 jsonl mtime 距今 <
# settle 秒就不桥接。client 不读 config.yaml 故吃 INGEST_SETTLE_SECONDS_DEFAULT
# （config.py=120.0），训练里每条轨迹白等 120s 才进 cc_sessions。调低到 ~15s。
INGEST_SETTLE="${XSKILL_INGEST_SETTLE:-15}"        # client 端 JsonlIngester settle 默认

# ── xskill / claude home 路径 ──────────────────────────────────────────
# server 端：HOME=$XHOME -> XSKILL_HOME=$XHOME/.xskill（config + skill repo +
#   team_server.json 在此）。daemon serve --server --home $HOME_ROOT -> epoch0
#   冷启动本机轨迹落 $HOME_ROOT/.claude/projects（被 watch）。
# server 的 skill 仓 = $XSKILL_SKILL_DIR；client reconcile 后把 main/staging 装
#   各 worker 自己 home/.claude/skills。
export XHOME=/root/xhome
export XSKILL_HOME="$XHOME/.xskill"
HOME_ROOT=/app/cchome
CHOME="$HOME_ROOT/.claude"
XSKILL_SKILL_DIR="$XSKILL_HOME/skill"      # 候选 + 毕业 + 灰度 skill 仓根（config.skill_dir）
TEAM_SERVER_JSON="$XSKILL_HOME/team_server.json"
mkdir -p "$XSKILL_HOME" "$CHOME/projects" "$CHOME/skills"

CLAUDE_ABS="$(command -v claude)"
[ -n "$CLAUDE_ABS" ] || { echo "FATAL: claude CLI not found on PATH"; sleep infinity; }

# log 写到 stderr：这样 collect_skills 等被 $(...) 捕获 stdout 的函数里调用 log
# 不会污染捕获值（N=$(collect_skills) 只拿到纯数字）；stderr 仍随容器 2>&1 入日志。
log() { echo "[$(date '+%F %T')] $*" >&2; }

# ── 0b) cgroup v2 检测 + worker 隔离 helper ──────────────────────────────
CG_ROOT=/sys/fs/cgroup
CG_OK=0
if grep -q 'cgroup2' /proc/mounts 2>/dev/null && [ -w "$CG_ROOT/cgroup.subtree_control" ]; then
  if echo "+cpu +memory" > "$CG_ROOT/cgroup.subtree_control" 2>/dev/null; then
    CG_OK=1
    log "cgroup v2 可用：已在 $CG_ROOT/cgroup.subtree_control 委派 +cpu +memory；worker 走 cgroup 硬隔离"
  else
    log "WARN: cgroup v2 已挂载但写 subtree_control 失败（控制器未委派？），worker 退化用 nice 软隔离"
  fi
else
  log "WARN: cgroup v2 不可用（容器非特权？无 cgroup2 挂载或 subtree_control 不可写），worker 退化用 nice 软隔离"
fi

# cg_exec <name> <cmd...> —— 在隔离环境里 exec 一条命令（用于子 shell 里）。
cg_exec() {
  local name=$1; shift
  if [ "$CG_OK" = "1" ]; then
    local cg="$CG_ROOT/$name"
    mkdir -p "$cg" 2>/dev/null || log "WARN: mkdir cgroup $cg 失败，worker 不隔离直跑"
    if [ -d "$cg" ]; then
      echo "$WORKER_CPU_MAX" > "$cg/cpu.max"    2>/dev/null || log "WARN: 写 $cg/cpu.max 失败（已忽略）"
      echo "$WORKER_MEM_MAX" > "$cg/memory.max" 2>/dev/null || log "WARN: 写 $cg/memory.max 失败（已忽略）"
      echo "$BASHPID"        > "$cg/cgroup.procs" 2>/dev/null || log "WARN: 迁 $BASHPID 进 $cg/cgroup.procs 失败（worker 不隔离直跑）"
    fi
    exec "$@"
  else
    exec nice -n 10 "$@"
  fi
}

# ── 1) 写 config.yaml（占位符替换真实 key）──────────────────────────────
log "writing xskill config -> $XSKILL_HOME/config.yaml"
sed -e "s#__XSKILL_HOME__#$XSKILL_HOME#g" \
    -e "s#__DEEPSEEK_API_KEY__#$DEEPSEEK_API_KEY#g" \
    -e "s#__DASHSCOPE_API_KEY__#$DASHSCOPE_API_KEY#g" \
    /app/config.yaml > "$XSKILL_HOME/config.yaml"

# ── 1a) canary 调低 + cold_start epoch 屏障 注入 config ──────────────────
# canary 调低（否则小数据永远凑不够样本）：probability=0.5 让 50% 流量进 staging，
#   min_samples/total_samples=2 让 2 条样本就能决策，scope_top_n=1。
# cold_start：enabled+flush_threshold=1+**epochs=1**（只 epoch0 走 cold_flush 屏障
#   毕业 baby->main；epoch1.. cold_flush 自然失效 -> SkillEdit 走在线 staging 路径）。
BARRIER_FILE="${XSKILL_BARRIER_FILE:-$HOME_ROOT/EPOCH_FLUSH}"
# 冷启动期默认关 description 触发优化（hill-climb）：批量毕业前跑 6 cases 探针拖慢
# flush；native 评测全量挂载不依赖 description 触发，对分数无影响。
COLD_DISABLE_DESC_OPT="${XSKILL_COLD_DISABLE_DESC_OPT:-true}"
python - "$XSKILL_HOME/config.yaml" "$BARRIER_FILE" "$COLD_DISABLE_DESC_OPT" <<'PY'
import sys, yaml
path, barrier, dis = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
c = yaml.safe_load(open(path, encoding="utf-8")) or {}
# canary 调低：让小数据灰度链路真能凑够样本并出决策
c["canary"] = {
    "enabled": True,
    "probability": 0.5,
    "min_samples": 2,
    "total_samples": 2,
    "scope_top_n": 1,
    "rotate_interval": 60,
    "max_days_hold": 14,
    # 综合分：0.5*val 正确率(归一 0-10) + 0.5*ux；.val_scores.json 由灰度 settle
    # 阶段 run_val_eval_for_staging 写入；缺 val 分的 side 退回纯 ux。
    "val_weight": 0.5,
}
# cold_start：epochs=1 -> 只 epoch0 落屏障 cold_flush 毕业 baby->main；epoch1.. 不再
# cold_flush，SkillEdit 走正常在线增量 -> commit_to_staging 开灰度。
c["cold_start"] = {"enabled": True, "flush_threshold": 1, "epochs": 1,
                   "barrier_path": barrier}
if dis:
    c.setdefault("skill_opt", {})["enabled"] = False
yaml.safe_dump(c, open(path, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
print("[config] canary lowered (prob=0.5 min/total=2 top_n=1); "
      "cold_start epochs=1 barrier=", barrier,
      "; skill_opt.enabled =", c.get("skill_opt", {}).get("enabled"))
PY
rm -f "$BARRIER_FILE"
log "config injected: canary(lowered) + cold_start(epochs=1) barrier=$BARRIER_FILE desc_opt_disabled=$COLD_DISABLE_DESC_OPT"

# ── 2) PATCH 毕业门槛：把 ATOM_PROMOTION_THRESHOLD 调到 $PROMO_THRESHOLD ──
CAND_PY="$(HOME=$XHOME python -c 'import xskill.skill.candidates as c; print(c.__file__)' 2>/dev/null)"
if [ -n "$CAND_PY" ] && [ -f "$CAND_PY" ]; then
  python - "$CAND_PY" "$PROMO_THRESHOLD" <<'PYEOF'
import re, sys
path, thr = sys.argv[1], int(sys.argv[2])
src = open(path, encoding="utf-8").read()
new, n = re.subn(r'^ATOM_PROMOTION_THRESHOLD\s*=\s*\d+',
                 f'ATOM_PROMOTION_THRESHOLD = {thr}', src, flags=re.M)
if n != 1:
    print(f"WARN: ATOM_PROMOTION_THRESHOLD not patched (matched {n}) in {path}")
    sys.exit(0)
open(path, "w", encoding="utf-8").write(new)
print(f"patched ATOM_PROMOTION_THRESHOLD -> {thr} in {path}")
PYEOF
  HOME=$XHOME python -c "import xskill.skill.candidates as c; print('[verify] ATOM_PROMOTION_THRESHOLD =', c.ATOM_PROMOTION_THRESHOLD)" 2>/dev/null \
    || log "WARN: could not verify patched threshold"
else
  log "WARN: could not locate candidates.py to patch promotion threshold"
fi

# ── 2b) PATCH team client 去抖默认（connect 不读 config/无 flag）──────────
# 把 TeamClient.__init__ 与 TeamCollector.__init__ 的 quiet_seconds /
# min_change_interval / poll_interval 默认值改小，让 worker 解题轨迹尽快上传。
DAEMON_FILE="$(HOME=$XHOME python -c 'import xskill.team.client.daemon as d; print(d.__file__)' 2>/dev/null)"
COLLECTOR_FILE="$(HOME=$XHOME python -c 'import xskill.team.client.collector as d; print(d.__file__)' 2>/dev/null)"
for f in "$DAEMON_FILE" "$COLLECTOR_FILE"; do
  [ -n "$f" ] && [ -f "$f" ] || { log "WARN: 找不到要 patch 的 team client 文件: $f"; continue; }
  python - "$f" "$CLIENT_QUIET" "$CLIENT_MIN_CHANGE" "$CLIENT_POLL" <<'PYEOF'
import re, sys
path, q, m, poll = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
src = open(path, encoding="utf-8").read()
n_total = 0
for key, val in (("quiet_seconds", q), ("min_change_interval", m), ("poll_interval", poll)):
    # 形如 `quiet_seconds: int = 180,` 或 `poll_interval: float = 30.0,`
    src, n = re.subn(rf'({key}\s*:\s*(?:int|float)\s*=\s*)[0-9.]+',
                     rf'\g<1>{val}', src)
    n_total += n
open(path, "w", encoding="utf-8").write(src)
print(f"patched {n_total} debounce default(s) in {path} "
      f"(quiet={q} min_change={m} poll={poll})")
PYEOF
done

# ── 2c) PATCH JsonlIngester settle barrier 默认（client 不读 config）─────────
# INGEST_SETTLE_SECONDS_DEFAULT 在 config.py，client 端 ingester 缺省吃它（120s）。
CONFIG_FILE="$(HOME=$XHOME python -c 'import xskill.config as c; print(c.__file__)' 2>/dev/null)"
if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
  python - "$CONFIG_FILE" "$INGEST_SETTLE" <<'PYEOF'
import re, sys
path, val = sys.argv[1], sys.argv[2]
src = open(path, encoding="utf-8").read()
src, n = re.subn(r'(INGEST_SETTLE_SECONDS_DEFAULT\s*=\s*)[0-9.]+',
                 rf'\g<1>{float(val)}', src)
open(path, "w", encoding="utf-8").write(src)
print(f"patched INGEST_SETTLE_SECONDS_DEFAULT -> {val} (matched {n}) in {path}")
PYEOF
else
  log "WARN: 找不到 config.py 来 patch INGEST_SETTLE_SECONDS_DEFAULT"
fi

# ── 3) seed server-side claude home settings.json（bypass perms + skill 预算）
cat > "$CHOME/settings.json" <<'JSON'
{"skillListingBudgetFraction":0.1,"permissions":{"defaultMode":"bypassPermissions"}}
JSON

# ── 4) 起 xskill daemon（serve --server：HTTP team server + server_mode watcher）
DAEMON_LOG=/tmp/xskill_daemon.log
log "starting xskill TEAM SERVER (HOME=$XHOME serve --server --home=$HOME_ROOT port=$DAEMON_PORT)"
HOME=$XHOME "$(command -v xskill)" --debug serve --server \
    --host 127.0.0.1 --port "$DAEMON_PORT" --home "$HOME_ROOT" \
    > "$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!
log "daemon pid=$DAEMON_PID"
sleep 12
if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
  log "FATAL: daemon exited within 12s — tail of $DAEMON_LOG:"
  tail -40 "$DAEMON_LOG"
  echo xskill > "$SKILL_OUT/ALGO"
  log "daemon dead at startup; sleeping (sidecar must not exit)"
  sleep infinity
fi
ensure_daemon() {
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    log "WATCHDOG: daemon died — restarting"
    HOME=$XHOME "$(command -v xskill)" --debug serve --server \
        --host 127.0.0.1 --port "$DAEMON_PORT" --home "$HOME_ROOT" \
        >> "$DAEMON_LOG" 2>&1 &
    DAEMON_PID=$!
    log "daemon restarted pid=$DAEMON_PID"; sleep 10
  fi
}

# ── 4b) 取 join token（team_server.json 的 join_token 字段）────────────────
TOKEN=""
for _ in $(seq 1 30); do
  if [ -f "$TEAM_SERVER_JSON" ]; then
    TOKEN="$(python -c "import json,sys;print(json.load(open('$TEAM_SERVER_JSON')).get('join_token',''))" 2>/dev/null)"
    [ -n "$TOKEN" ] && break
  fi
  sleep 1
done
if [ -n "$TOKEN" ]; then
  log "team server join_token acquired (len=${#TOKEN}) from $TEAM_SERVER_JSON"
else
  log "WARN: could not read join_token from $TEAM_SERVER_JSON after 30s — connect 将失败（team-CS epoch 无法上传）"
fi

# ── collect helper：把蒸馏出的 SKILL.md（main 分支）收集到 /shared/skill/skills/ ─
GIT="$(command -v git)"
is_stub() {  # $1=SKILL.md path; 0=stub, 1=real
  grep -q '(placeholder —' "$1" 2>/dev/null && return 0
  return 1
}
collect_skills() {
  local mode_count=0
  rm -rf "$SKILLS_OUT"; mkdir -p "$SKILLS_OUT"
  [ -d "$XSKILL_SKILL_DIR" ] || { echo 0; return; }

  # ---- pass A: main 分支 ----
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d" ] || continue
    local name; name=$(basename "$d")
    [ "${name#.}" != "$name" ] && continue
    [ -f "$d/SKILL.md" ] || continue
    local br=""
    [ -d "$d/.git" ] && [ -n "$GIT" ] && br=$("$GIT" -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
    [ "$br" = "main" ] || continue
    mkdir -p "$SKILLS_OUT/$name"
    cp "$d/SKILL.md" "$SKILLS_OUT/$name/SKILL.md"
    for ex in references scripts assets templates; do
      [ -d "$d/$ex" ] && cp -r "$d/$ex" "$SKILLS_OUT/$name/$ex" 2>/dev/null
    done
    mode_count=$((mode_count+1))
  done
  if [ "$mode_count" -gt 0 ]; then
    log "collect: shipped $mode_count MAIN-branch (graduated) skill(s)"
    echo "$mode_count"; return
  fi

  # ---- pass B: 任何非 stub 正文（不论分支）----
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d" ] || continue
    local name; name=$(basename "$d")
    [ "${name#.}" != "$name" ] && continue
    [ -f "$d/SKILL.md" ] || continue
    is_stub "$d/SKILL.md" && continue
    mkdir -p "$SKILLS_OUT/$name"
    cp "$d/SKILL.md" "$SKILLS_OUT/$name/SKILL.md"
    for ex in references scripts assets templates; do
      [ -d "$d/$ex" ] && cp -r "$d/$ex" "$SKILLS_OUT/$name/$ex" 2>/dev/null
    done
    mode_count=$((mode_count+1))
  done
  if [ "$mode_count" -gt 0 ]; then
    log "collect: no MAIN skills; shipped $mode_count non-stub SKILL.md (real distilled body, branch!=main)"
    echo "$mode_count"; return
  fi

  # ---- pass C: 最后兜底（baby stub）----
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d" ] || continue
    local name; name=$(basename "$d")
    [ "${name#.}" != "$name" ] && continue
    [ -f "$d/SKILL.md" ] || continue
    mkdir -p "$SKILLS_OUT/$name"
    cp "$d/SKILL.md" "$SKILLS_OUT/$name/SKILL.md"
    mode_count=$((mode_count+1))
  done
  [ "$mode_count" -gt 0 ] && log "collect: FALLBACK shipped $mode_count baby STUB skill(s) (no real distillation graduated)"
  echo "$mode_count"
}

snapshot() {  # 打印当前 skill 仓状态（含 staging 分支与灰度物化目录，调试）
  [ -d "$XSKILL_SKILL_DIR" ] || { log "  (skill dir not yet created)"; return; }
  local any=0
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d" ] || continue
    local name; name=$(basename "$d")
    [ "${name#.}" != "$name" ] && continue
    local br="-"; [ -d "$d/.git" ] && [ -n "$GIT" ] && br=$("$GIT" -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
    local has_st="no"
    [ -d "$d/.git" ] && [ -n "$GIT" ] && "$GIT" -C "$d" rev-parse --verify staging >/dev/null 2>&1 && has_st="YES"
    local kind="stub"; [ -f "$d/SKILL.md" ] && ! is_stub "$d/SKILL.md" && kind="REAL"
    log "  skill: $name [branch=$br staging=$has_st body=$kind]"
    any=1
  done
  [ "$any" = 0 ] && log "  (no skills yet)"
}

# ── 5) team-CS worker（**所有 epoch 含 epoch0**）：独立 home + connect client + 上传 ─
# 重要架构事实：serve --server（server_mode）下 watcher **只消费 client 经
# /api/v1/team/upload 上传的轨迹**（落 team_trajectories/clients/<id>/sessions
# 并注册成 watch_dir），**不 ingest server 本机 --home 的 .claude/projects**
# （api/app.py 的 _ensure_ingesters_for_detected_ecosystems 开头 `if team_server:
# return`）。故 epoch0 冷启动也必须走 team client 上传——否则 server 永远看不到
# epoch0 轨迹、零 atom、零毕业。epoch0 与 epoch1.. 的唯一区别是：epoch0 末落屏障
# 触发 cold_flush 批量毕业 baby->main；epoch1.. 不落屏障走在线 staging 灰度。
# 每个 worker_k:
# 每个 worker_k:
#   * 独立 HOME=/root/whome_$k（.claude/projects+skills+settings.json）。
#   * 启动一个常驻 `xskill connect --label worker$k` client（HOME=$WHOME），
#     它持续把 $WHOME/.claude/projects 的轨迹镜像+上传 server、并把 server 分给
#     该 client 的 skill side(main/staging) 装到 $WHOME/.claude/skills。
#   * 用 multi_turn_rollout（--chome $WHOME/.claude）让该 worker 的 claude 解题
#     ——claude 加载 $WHOME/.claude/skills 里 client 装好的技能（native 全量挂载），
#     解题轨迹落 $WHOME/.claude/projects -> client 上传。
declare -A WORKER_CLIENT_PID   # worker_k -> connect client pid

whome_for() { echo "/root/whome_$1"; }

setup_worker_home() {  # $1=worker_k —— 建独立 home + settings，幂等
  local k=$1 wh; wh="$(whome_for "$k")"
  mkdir -p "$wh/.claude/projects" "$wh/.claude/skills" "$wh/.xskill"
  cat > "$wh/.claude/settings.json" <<'JSON'
{"skillListingBudgetFraction":0.1,"permissions":{"defaultMode":"bypassPermissions"}}
JSON
}

start_worker_client() {  # $1=worker_k —— 起常驻 connect client（若未起）
  local k=$1 wh; wh="$(whome_for "$k")"
  [ -n "$TOKEN" ] || { log "WARN: 无 join_token，worker$k connect 跳过（无法上传）"; return; }
  # 已在跑则不重起
  local pid="${WORKER_CLIENT_PID[$k]:-}"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then return; fi
  local clog="/tmp/client_w${k}.log"
  # 子壳：HOME=$WHOME 让 client 的 ingester/install 都锚在该 worker home；
  # 首次带 address+token 注册（distinct --label worker$k -> distinct client_id）。
  (
    export HOME="$wh"
    cg_exec "xskill_cli${k}" \
      "$(command -v xskill)" --debug connect "127.0.0.1:$DAEMON_PORT" \
        --token "$TOKEN" --label "worker$k"
  ) > "$clog" 2>&1 &
  WORKER_CLIENT_PID[$k]=$!
  log "  [client] worker$k connect started pid=${WORKER_CLIENT_PID[$k]} HOME=$wh log=$clog"
}

run_item_team() {  # $1=idx $2=id $3=worker_k —— team-CS 解题（写 worker home）
  local idx=$1 id=$2 k=$3 wh; wh="$(whome_for "$k")"
  local ws="/tmp/ws_e${ep}_w${k}_${id}"
  local out="/tmp/mtres_e${ep}_${id}.json"
  local logf="/tmp/mtres_e${ep}_${id}.log"
  rm -rf "$ws"; mkdir -p "$ws"
  log "  [ep$ep w$k] TEAM rollout item $idx id=$id (chome=$wh/.claude max_turns=$MAX_TURNS timeout=${ITEM_TIMEOUT}s)"
  (
    export CLAUDE_CONFIG_DIR="$wh/.claude"
    export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
    export ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY"
    export CLAUDE_CODE_EXEC_USE_SDK=cli
    export IS_SANDBOX=1
    export XSKILL_SKILL_MODE=native
    cg_exec "xskill_w${k}" \
      python /app/multi_turn_rollout.py \
        --item-id "$id" --data-root "$DATA_ROOT" --chome "$wh/.claude" \
        --claude-path "$CLAUDE_ABS" --model "$EVAL_MODEL" \
        --max-turns "$MAX_TURNS" \
        --workspace "$ws" --out "$out" --timeout "$ITEM_TIMEOUT"
  ) > "$logf" 2>&1
  local rc=$?
  if [ -f "$out" ]; then
    local succ turns; succ=$(python -c "import json;print(json.load(open('$out')).get('success'))" 2>/dev/null || echo NA)
    turns=$(python -c "import json;print(json.load(open('$out')).get('turns_used'))" 2>/dev/null || echo NA)
    log "  [ep$ep w$k] item $idx id=$id -> done (success=$succ turns=$turns rc=$rc)"
  else
    log "  [ep$ep w$k] item $idx id=$id -> no result json (rc=$rc); tail:"
    tail -8 "$logf" 2>/dev/null | while IFS= read -r l; do log "    mt| $l"; done
  fi
}

# ── 5c) 一个 epoch：并行跑全部 TRAIN_IDS（分批 worker，显式 PID wait）─────────
# 关键：只 wait 显式 worker rollout PID（绝不裸 wait——会等永不退出的 daemon +
# connect client -> 死锁）。worker slot k 在 0..WORKERS-1 轮转。
run_epoch() {  # 所有 epoch 都走 team client 上传路径
  local idx=0 slot=0
  local pids=()
  for id in "${TRAIN_IDS[@]}"; do
    idx=$((idx+1))
    run_item_team "$idx" "$id" "$slot" &
    pids+=("$!")
    slot=$(( (slot+1) % WORKERS ))
    if [ "${#pids[@]}" -ge "$WORKERS" ]; then
      wait "${pids[@]}"
      pids=()
    fi
  done
  [ "${#pids[@]}" -gt 0 ] && wait "${pids[@]}"
  return 0
}

# poll daemon 日志里的 canary / staging / 毕业关键行（在线 epoch settle 时观测）
poll_canary_log() {  # $1=label
  local label=$1
  log "  [$label] daemon canary/staging/graduation lines (tail):"
  grep -aiE 'commit_to_staging|staging|canary|promot|merge_staging|discard_staging|🎓|graduat|baby.*main|CS (attribution|score)' \
    "$DAEMON_LOG" 2>/dev/null | tail -25 | while IFS= read -r l; do log "    cs| $l"; done
}

# ── 5e) val 集解题正确率评测（给 canary 综合分喂 val_score）─────────────────
# 设计（why）：canary 晋升比较的不再是纯 ux_score，而是综合分 =
#   0.5*(val 集解题正确率, 归一化 0-10) + 0.5*(ux_score)。
# SkillOpt 用 val 做监督优化，我们之前完全没用 val；这里在每个灰度 epoch 的
# settle 阶段，对 server 技能仓里每个有 staging 分支的技能，分别用 **main 版** 和
# **staging 版** 技能去解 val 10 题、算正确率，按该版本的 git commit sha 写进
# 该技能目录的 .val_scores.json（结构 {"<sha>":{"acc":..,"n":..}}）。下一轮
# watcher tick 的 _check_canary_decisions 读它用综合分裁决。
#
# 怎么"用某个版本技能解 val"（最简方案）：技能是 git 分支。对每个 side：
#   1. 在该技能子仓里 `git show <branch>:SKILL.md` 物化到一个**隔离 val HOME**的
#      .claude/skills/<name>/（该 HOME 不在 server watch 范围、也不是任何 connect
#      client 的 home，故 val 解题轨迹绝不被当训练数据入库 / 污染 A/B）。
#   2. 用 multi_turn_rollout.py --max-turns 1（single turn：val 测正确率不是产
#      轨迹）对 val 10 题各解一遍，读 result json 的 success 算 acc=对数/10。
#   3. 写 .val_scores.json[<sha>]={acc,n=10}。
# val 解题带与 rollout 同一套 deepseek env（ANTHROPIC_BASE_URL / AUTH_TOKEN）。
VAL_ITEMS_JSON="${VAL_ITEMS_JSON:-$TRAIN_SPLIT/val/items.json}"
VAL_TIMEOUT="${XSKILL_VAL_TIMEOUT:-300}"     # 单 val item single-turn 超时
VAL_HOME_ROOT="${XSKILL_VAL_HOME_ROOT:-/root/valhome}"  # 隔离、未注册 watch 的 HOME 根
VAL_ENABLE="${XSKILL_VAL_ENABLE:-true}"

mapfile -t VAL_IDS < <(python -c "import json;[print(i['id']) for i in json.load(open('$VAL_ITEMS_JSON'))]" 2>/dev/null)

# 把某技能子仓某分支的 SKILL.md(+references/scripts/assets/templates) 物化到 dst/<name>/
materialize_side_skill() {  # $1=skill_subrepo $2=branch $3=dst_skills_dir $4=name
  local d=$1 br=$2 dst=$3 name=$4
  rm -rf "$dst/$name"; mkdir -p "$dst/$name"
  if ! "$GIT" -C "$d" show "$br:SKILL.md" > "$dst/$name/SKILL.md" 2>/dev/null; then
    "$GIT" -C "$d" show "$br:skill.md" > "$dst/$name/SKILL.md" 2>/dev/null || return 1
  fi
  # 附带目录（best-effort：用 git archive 抽该分支的子树）
  for ex in references scripts assets templates; do
    "$GIT" -C "$d" archive "$br" "$ex" 2>/dev/null | tar -x -C "$dst/$name" 2>/dev/null || true
  done
  return 0
}

# 在隔离 HOME 里对 val 全集单轮解题，回显 acc（对数/总数）
eval_val_acc() {  # $1=val_home（其 .claude/skills 已物化好该 side 技能）
  local vh=$1 correct=0 total=0 id ws out
  for id in "${VAL_IDS[@]}"; do
    total=$((total+1))
    ws="/tmp/valws_$$_${id}"; out="/tmp/valres_$$_${id}.json"
    rm -rf "$ws"; mkdir -p "$ws"
    (
      export CLAUDE_CONFIG_DIR="$vh/.claude"
      export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
      export ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY"
      export CLAUDE_CODE_EXEC_USE_SDK=cli
      export IS_SANDBOX=1
      export XSKILL_SKILL_MODE=native
      python /app/multi_turn_rollout.py \
        --item-id "$id" --data-root "$DATA_ROOT" --chome "$vh/.claude" \
        --claude-path "$CLAUDE_ABS" --model "$EVAL_MODEL" \
        --max-turns 1 \
        --workspace "$ws" --out "$out" --timeout "$VAL_TIMEOUT"
    ) >/dev/null 2>&1
    if [ -f "$out" ]; then
      local succ; succ=$(python -c "import json;print(1 if json.load(open('$out')).get('success') else 0)" 2>/dev/null || echo 0)
      correct=$((correct+succ))
    fi
    rm -rf "$ws" "$out"
  done
  python -c "print(round($correct/$total,4) if $total else 0.0)"
}

# 对每个有 staging 分支的技能，main+staging 各跑 val，写 .val_scores.json
run_val_eval_for_staging() {  # $1=label
  local label=$1
  [ "$VAL_ENABLE" = "true" ] || { log "  [$label] val eval disabled (XSKILL_VAL_ENABLE!=true)"; return; }
  [ -d "$XSKILL_SKILL_DIR" ] && [ -n "$GIT" ] || { log "  [$label] val eval skip (no skill dir / git)"; return; }
  [ "${#VAL_IDS[@]}" -gt 0 ] || { log "  [$label] val eval skip (no val items in $VAL_ITEMS_JSON)"; return; }
  local d name has_st m_sha s_sha
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d/.git" ] || continue
    name=$(basename "$d"); [ "${name#.}" != "$name" ] && continue
    "$GIT" -C "$d" rev-parse --verify staging >/dev/null 2>&1 || continue  # 只评有 staging 的
    m_sha=$("$GIT" -C "$d" rev-parse main 2>/dev/null)
    s_sha=$("$GIT" -C "$d" rev-parse staging 2>/dev/null)
    [ -n "$m_sha" ] && [ -n "$s_sha" ] || continue
    log "  [$label] VAL-EVAL skill=$name main=${m_sha:0:8} staging=${s_sha:0:8} (val=${#VAL_IDS[@]} items single-turn)"
    local side sha acc
    for side in main staging; do
      [ "$side" = main ] && sha=$m_sha || sha=$s_sha
      local vh="$VAL_HOME_ROOT/${name}_${side}"
      rm -rf "$vh"; mkdir -p "$vh/.claude/projects" "$vh/.claude/skills"
      cat > "$vh/.claude/settings.json" <<'JSON'
{"skillListingBudgetFraction":0.1,"permissions":{"defaultMode":"bypassPermissions"}}
JSON
      if ! materialize_side_skill "$d" "$side" "$vh/.claude/skills" "$name"; then
        log "    [$label] $name/$side: no SKILL.md on branch, skip"; continue
      fi
      acc=$(eval_val_acc "$vh")
      # 写 .val_scores.json[<sha>]={acc,n}（合并已有条目，按 sha 幂等覆盖本 sha）
      python - "$d/.val_scores.json" "$sha" "$acc" "${#VAL_IDS[@]}" <<'PY'
import json, os, sys
path, sha, acc, n = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
data = {}
if os.path.exists(path):
    try: data = json.load(open(path, encoding="utf-8")) or {}
    except Exception: data = {}
data[sha] = {"acc": acc, "n": n}
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"[val] {os.path.basename(os.path.dirname(path))} {sha[:8]} acc={acc} n={n}")
PY
      log "    [$label] $name/$side acc=$acc → wrote .val_scores.json[$sha]"
      rm -rf "$vh"
    done
  done
}

# ── 6) 多 epoch 训练循环 ────────────────────────────────────────────────
mapfile -t TRAIN_IDS < <(python -c "import json;[print(i['id']) for i in json.load(open('$TRAIN_SPLIT/train/items.json'))]")
log "train items = ${#TRAIN_IDS[@]} :: ${TRAIN_IDS[*]}; EPOCHS=$EPOCHS WORKERS=$WORKERS MAX_TURNS=$MAX_TURNS CANARY_SETTLE=$CANARY_SETTLE"

# epoch0 屏障 flush + poll 等 daemon 消费（消费=baby->main 批量毕业完成信号）
FLUSH_WAIT="${XSKILL_FLUSH_WAIT:-600}"
flush_barrier() {
  touch "$BARRIER_FILE"
  log "epoch0 done → barrier dropped $BARRIER_FILE; waiting up to ${FLUSH_WAIT}s for cold flush (baby→main)"
  local consumed=0 _
  for _ in $(seq 1 "$FLUSH_WAIT"); do
    ensure_daemon
    if [ ! -f "$BARRIER_FILE" ]; then consumed=1; log "barrier consumed → epoch0 cold flush done"; break; fi
    sleep 1
  done
  [ "$consumed" = 1 ] || log "WARN: barrier still present after ${FLUSH_WAIT}s (cold flush may be incomplete)"
  sleep 10; ensure_daemon
}

# 所有 epoch 起头都先拉起 worker 独立 home + 常驻 connect client（幂等：已跑则跳过）。
# epoch0 也必须有 client 在跑——server_mode 只 ingest client 上传的轨迹。
start_all_clients() {
  for k in $(seq 0 $((WORKERS-1))); do
    setup_worker_home "$k"
    start_worker_client "$k"
  done
}

# 分段 poll settle：每 ~settle/6 打一次 canary/staging 日志，给闭环足够 watcher tick。
settle_with_polling() {  # $1=secs $2=label
  local total=$1 label=$2 seg elapsed=0
  seg=$(( total / 6 )); [ "$seg" -lt 20 ] && seg=20
  while [ "$elapsed" -lt "$total" ]; do
    sleep "$seg"; elapsed=$((elapsed+seg)); ensure_daemon
    poll_canary_log "$label +${elapsed}s"
  done
}

for ep in $(seq 0 $((EPOCHS-1))); do
  ensure_daemon
  start_all_clients
  sleep 8   # 给 client 一拍完成 register + 首次 sync（装好已毕业 main 技能）
  if [ "$ep" -eq 0 ]; then
    # ── epoch0：冷启动（client 上传 -> server cold_flush 毕业 baby→main）──
    log "=== epoch 0 / $((EPOCHS-1)) :: COLD-START via team clients (upload → baby→main graduation via barrier) ==="
    run_epoch
    log "epoch0 rollout done; settle ${FINAL_SETTLE}s for upload(debounce)→ingest→cluster before barrier"
    settle_with_polling "$FINAL_SETTLE" "ep0-settle"
    flush_barrier
    snapshot
    log "=== epoch0 graduation snapshot above ==="
  else
    # ── epoch1..N-1：team-CS 灰度在线进化（不落屏障，cold_flush 已在 epoch0 耗尽）──
    log "=== epoch $ep / $((EPOCHS-1)) :: TEAM-CS canary online evolution (WORKERS=$WORKERS clients) ==="
    run_epoch
    # settle 切两段：前半让 upload→distill→score→SkillEdit 把"已有 main 技能的
    # 更新"路由到 staging 并攒 ux；中途跑 val 评测写 .val_scores.json；后半让
    # _check_canary_decisions 读 ux+val 综合分裁决。给两段都留足 watcher tick。
    VAL_SETTLE=$(( CANARY_SETTLE / 2 )); [ "$VAL_SETTLE" -lt 60 ] && VAL_SETTLE=60
    DECIDE_SETTLE=$(( CANARY_SETTLE - VAL_SETTLE )); [ "$DECIDE_SETTLE" -lt 60 ] && DECIDE_SETTLE=60
    log "epoch $ep rollout done; phase-A settle ${VAL_SETTLE}s (upload→distill→score→staging open)"
    settle_with_polling "$VAL_SETTLE" "ep$ep-A"
    log "epoch $ep: running val eval on staging-bearing skills (writes .val_scores.json)"
    run_val_eval_for_staging "ep$ep-val"
    log "epoch $ep: phase-B settle ${DECIDE_SETTLE}s (canary composite decision: 0.5*val + 0.5*ux)"
    settle_with_polling "$DECIDE_SETTLE" "ep$ep-B"
    snapshot
  fi
done

log "=== final skill-repo snapshot ==="
snapshot
log "=== daemon log tail (cluster / SkillEdit / staging / canary / graduation) ==="
grep -aiE 'cluster|skilledit|skill_edit|graduat|baby|promot|atom|staging|canary|commit_to_staging|merge_staging|discard_staging|🎓|🌱' \
  "$DAEMON_LOG" 2>/dev/null | tail -40 \
  || tail -40 "$DAEMON_LOG" 2>/dev/null

# git log per skill（看 staging 分支 + 晋升痕迹）
log "=== per-skill git log (branches + recent commits) ==="
if [ -d "$XSKILL_SKILL_DIR" ] && [ -n "$GIT" ]; then
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d/.git" ] || continue
    name=$(basename "$d")
    log "  --- $name branches ---"
    "$GIT" -C "$d" branch -a 2>/dev/null | while IFS= read -r l; do log "    br| $l"; done
    "$GIT" -C "$d" log --oneline --all -8 2>/dev/null | while IFS= read -r l; do log "    lg| $l"; done
  done
fi

N=$(collect_skills)
log "collected $N skill folder(s) into $SKILLS_OUT"

echo xskill > "$SKILL_OUT/ALGO"

if [ "${N:-0}" -gt 0 ] && find "$SKILLS_OUT" -name SKILL.md | grep -q .; then
  touch "$SKILL_OUT/DONE"
  log "DONE: $(find "$SKILLS_OUT" -name SKILL.md | wc -l) SKILL.md under $SKILLS_OUT"
  find "$SKILLS_OUT" -name SKILL.md | while IFS= read -r f; do log "  -> $f"; done
else
  log "FATAL: collected zero SKILL.md — not writing DONE. Sleeping (sidecar must not exit)."
fi

# ── /output 交付 ────────────────────────────────────────────────────────
OUTPUT_DIR="${OUTPUT_DIR:-/shared/out}"
mkdir -p "$OUTPUT_DIR/algo" "$OUTPUT_DIR/trajectories" "$OUTPUT_DIR/rollout_results" 2>/dev/null || true
[ -d "$XSKILL_SKILL_DIR" ] && cp -a "$XSKILL_SKILL_DIR" "$OUTPUT_DIR/algo/skill_repo" 2>/dev/null || true
cp -a "$DAEMON_LOG" "$OUTPUT_DIR/algo/" 2>/dev/null || true
cp -a /tmp/mtres_*.log "$OUTPUT_DIR/algo/" 2>/dev/null || true
cp -a /tmp/client_w*.log "$OUTPUT_DIR/algo/" 2>/dev/null || true
cp -a "$SKILLS_OUT" "$OUTPUT_DIR/algo/skills_shipped" 2>/dev/null || true
cp -a /tmp/mtres_*.json "$OUTPUT_DIR/rollout_results/" 2>/dev/null || true
# 所有 worker home + server home 产出的轨迹 jsonl（递归收集，扁平化）
for proj in "$CHOME/projects" /root/whome_*/.claude/projects; do
  [ -d "$proj" ] || continue
  find "$proj" -name '*.jsonl' 2>/dev/null | while IFS= read -r jf; do
    rel=$(echo "$jf" | sed "s#^/##; s#/#__#g")
    cp -a "$jf" "$OUTPUT_DIR/trajectories/$rel" 2>/dev/null || true
  done
done
NTRAJ=$(find "$OUTPUT_DIR/trajectories" -name '*.jsonl' 2>/dev/null | wc -l)
NRES=$(find "$OUTPUT_DIR/rollout_results" -name '*.json' 2>/dev/null | wc -l)
log "copied algo artifacts -> $OUTPUT_DIR/algo; trajectories=$NTRAJ jsonl; rollout_results=$NRES json"

# sidecar restartPolicy:Always —— 永不退出
log "entrypoint reached steady state; sleep infinity"
sleep infinity
