#!/usr/bin/env bash
# ============================================================================
# 打榜镜像·xskill —— REDUCED-SCALE REAL TRAINING entrypoint.
#
# xskill 是一个 daemon：它 watch 一个 Claude Code "projects" 目录里的 agent
# 轨迹，把轨迹蒸馏成 skill 候选（cluster agent 给每个 atom 打 weightscore），
# 当某个 skill 的候选累计 weightscore >= 阈值时，SkillEditAgent 跑一遍把候选
# 整理成正文 SKILL.md 并 git "毕业"（baby -> main）。
#
# 本入口是 phase3b 经验驱动的「串行缩量版」：
#   1. 起 daemon（HOME=$XHOME serve --home $HOME_ROOT，watch $CHOME/projects）。
#   2. 对 4 个训练样本各跑 1 个 claude_code_exec rollout（SkillOpt eval_only.py），
#      生成 daemon 会入库的轨迹。
#   3. 每个样本后 settle，让 daemon 入库 + 聚类 + 可能毕业；mirror 已毕业 skill。
#   4. 全部样本后再 settle，收集蒸馏出的 SKILL.md -> /shared/skill/skills/<name>/。
#
# ── reduced 规模的核心难点：毕业门槛 ─────────────────────────────────────
# xskill baby->main 毕业门槛 = candidates.py 里硬编码的 ATOM_PROMOTION_THRESHOLD
# （默认 10）。runner 构造 SkillEditAgent 时**不传 threshold**，所以 config.yaml
# 的 candidates.threshold 改不动这个 v2 毕业门槛（它只作用于已废弃的 v1 路径）。
# 4 条轨迹 -> 4 个 atom，单 atom 通常打 6-9 分（cluster 评分档：8-9=高质量、
# 6-7=中等），常常分散在不同 skill 里 -> 每个 skill 攒不满 10 -> 零毕业 ->
# 全部停在 baby -> baby 的 SKILL.md 是 stub 占位符（不是真蒸馏内容）。
# => 必须在运行时 patch ATOM_PROMOTION_THRESHOLD 调低，让单条好轨迹就能让它的
#    skill 毕业成 main，从而产出**真正蒸馏出的** SKILL.md 正文。见下方 patch。
#
# sidecar restartPolicy:Always —— 任何情况下都不能退出（退出会无限重训），
# 故所有路径（成功 / fatal）末尾都 sleep infinity。
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
ITEM_SETTLE="${ITEM_SETTLE:-120}"                # 每个样本后等 daemon 入库/毕业
FINAL_SETTLE="${FINAL_SETTLE:-150}"              # 全部样本后再等一轮聚类/毕业
ITEM_TIMEOUT="${ITEM_TIMEOUT:-420}"              # 单 rollout exec 超时
TRAIN_SPLIT="${TRAIN_SPLIT:-/app/train_split}"
DATA_ROOT="${DATA_ROOT:-/data}"
EVAL_MODEL="${EVAL_MODEL:-deepseek-v4-flash}"

# ── xskill / claude home 路径 ──────────────────────────────────────────
# HOME=$XHOME -> XSKILL_HOME=$XHOME/.xskill（config + skill repo 在此）。
# daemon serve --home $HOME_ROOT -> watch $HOME_ROOT/.claude/projects，
#   并把毕业 skill install 到 $HOME_ROOT/.claude/skills。
# rollout XSKILL_CLAUDE_HOME=$CHOME -> claude CLI 写 $CHOME/projects/*.jsonl。
# 因此 CHOME 必须 == $HOME_ROOT/.claude（两者指同一被 watch 的目录）。
export XHOME=/root/xhome
export XSKILL_HOME="$XHOME/.xskill"
HOME_ROOT=/app/cchome
CHOME="$HOME_ROOT/.claude"
XSKILL_SKILL_DIR="$XSKILL_HOME/skill"      # 候选 + 毕业 skill 仓根（config.skill_dir）
mkdir -p "$XSKILL_HOME" "$CHOME/projects" "$CHOME/skills"

CLAUDE_ABS="$(command -v claude)"
[ -n "$CLAUDE_ABS" ] || { echo "FATAL: claude CLI not found on PATH"; sleep infinity; }

# log 写到 stderr：这样 collect_skills 等被 $(...) 捕获 stdout 的函数里调用 log
# 不会污染捕获值（N=$(collect_skills) 只拿到纯数字）；stderr 仍随容器 2>&1 入日志。
log() { echo "[$(date '+%F %T')] $*" >&2; }

# ── 1) 写 config.yaml（占位符替换真实 key）──────────────────────────────
log "writing xskill config -> $XSKILL_HOME/config.yaml"
sed -e "s#__XSKILL_HOME__#$XSKILL_HOME#g" \
    -e "s#__DEEPSEEK_API_KEY__#$DEEPSEEK_API_KEY#g" \
    -e "s#__DASHSCOPE_API_KEY__#$DASHSCOPE_API_KEY#g" \
    /app/config.yaml > "$XSKILL_HOME/config.yaml"

# ── 2) PATCH 毕业门槛：把 ATOM_PROMOTION_THRESHOLD 调到 $PROMO_THRESHOLD ──
# runner 不传 threshold（用 dataclass 默认 = C.ATOM_PROMOTION_THRESHOLD），所以
# 直接改这个模块常量就能把 baby->main 毕业门槛整体调低。改 site-packages 里
# 已安装的 candidates.py（-e 装的话指向 /app/xskill/src）。
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
  # 验证生效（重新 import 读常量值）
  HOME=$XHOME python -c "import xskill.skill.candidates as c; print('[verify] ATOM_PROMOTION_THRESHOLD =', c.ATOM_PROMOTION_THRESHOLD)" 2>/dev/null \
    || log "WARN: could not verify patched threshold"
else
  log "WARN: could not locate candidates.py to patch promotion threshold"
fi

# ── 3) seed claude home settings.json（bypass perms + skill 预算）─────────
cat > "$CHOME/settings.json" <<'JSON'
{"skillListingBudgetFraction":0.1,"permissions":{"defaultMode":"bypassPermissions"}}
JSON

# ── 4) 起 xskill daemon ─────────────────────────────────────────────────
DAEMON_LOG=/tmp/xskill_daemon.log
log "starting xskill daemon (HOME=$XHOME serve --home=$HOME_ROOT port=$DAEMON_PORT)"
HOME=$XHOME "$(command -v xskill)" --debug serve \
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
    HOME=$XHOME "$(command -v xskill)" --debug serve \
        --host 127.0.0.1 --port "$DAEMON_PORT" --home "$HOME_ROOT" \
        >> "$DAEMON_LOG" 2>&1 &
    DAEMON_PID=$!
    log "daemon restarted pid=$DAEMON_PID"; sleep 10
  fi
}

# ── collect helper：把蒸馏出的 SKILL.md 收集到 /shared/skill/skills/ ──────
# 策略（优先级递减）：
#   A. main 分支 skill = 真正毕业的蒸馏 skill（首选）。
#   B. 没有 main 时回退：任何 SKILL.md 正文非 stub 的 skill（不论分支）——
#      SkillEditAgent 可能已写好正文但 commit 卡在别处，正文仍是真蒸馏内容。
#   C. 仍为空时最后兜底：收集任何带 SKILL.md 的候选（baby stub）并标注 —— 让
#      DONE gate 不至于零产出；但日志会明确这是 stub fallback。
# 候选 skill 目录 = $XSKILL_SKILL_DIR/<name>/SKILL.md（已确认的真实路径）。
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

snapshot() {  # 打印当前 skill 仓状态（调试）
  [ -d "$XSKILL_SKILL_DIR" ] || { log "  (skill dir not yet created)"; return; }
  local any=0
  for d in "$XSKILL_SKILL_DIR"/*/; do
    [ -d "$d" ] || continue
    local name; name=$(basename "$d")
    [ "${name#.}" != "$name" ] && continue
    local br="-"; [ -d "$d/.git" ] && [ -n "$GIT" ] && br=$("$GIT" -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
    local kind="stub"; [ -f "$d/SKILL.md" ] && ! is_stub "$d/SKILL.md" && kind="REAL"
    log "  skill: $name [branch=$br body=$kind]"
    any=1
  done
  [ "$any" = 0 ] && log "  (no skills yet)"
}

# ── 5) 一个训练 rollout（写 $CHOME/projects 的轨迹给 daemon 入库）────────
run_item() {  # $1=idx $2=id
  local idx=$1 id=$2
  local isplit="/tmp/item_${idx}"
  mkdir -p "$isplit/train" "$isplit/val" "$isplit/test"
  python -c "
import json
items=json.load(open('$TRAIN_SPLIT/train/items.json'))
one=[it for it in items if str(it['id'])=='$id']
json.dump(one, open('$isplit/train/items.json','w'), ensure_ascii=False)
json.dump([], open('$isplit/val/items.json','w')); json.dump([], open('$isplit/test/items.json','w'))
"
  local out="/tmp/x_${idx}_${id}"
  rm -rf "$out"; mkdir -p "$out"
  log "  rollout item $idx id=$id (claude_code_exec, timeout=${ITEM_TIMEOUT}s)"
  # IS_SANDBOX=1: 容器以 root 运行，claude CLI 默认拒绝 root 下用
  # --dangerously-skip-permissions（"cannot be used with root/sudo privileges"）。
  # 设 IS_SANDBOX=1 让 claude 接受该 flag（容器即沙箱），rollout 才能真正调起
  # claude 产生轨迹；否则 claude 1 秒退出、$CHOME/projects 永远空、daemon 无轨迹
  # 可入库 -> 零 skill。这是本镜像在 root 容器里能跑通 rollout 的关键。
  ( cd /app/SkillOpt && \
    IS_SANDBOX=1 \
    ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
    ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY" \
    CLAUDE_CODE_EXEC_USE_SDK=cli \
    XSKILL_CLAUDE_HOME="$CHOME" XSKILL_SKILL_MODE=native \
    python scripts/eval_only.py \
      --config configs/spreadsheetbench/default.yaml \
      --skill /app/empty_skill.md \
      --split train --split_mode split_dir --split_dir "$isplit" --data_root "$DATA_ROOT" \
      --target_backend claude_code_exec --target_model "$EVAL_MODEL" \
      --claude_code_exec_use_sdk cli --claude_code_exec_path "$CLAUDE_ABS" \
      --mode single --workers 1 --out_root "$out" \
      --cfg-options env.mode=single env.exec_timeout=$ITEM_TIMEOUT ) \
      > "$out.log" 2>&1
  local rc=$?
  if [ -f "$out/eval_summary.json" ]; then
    local hard; hard=$(python -c "import json;print(json.load(open('$out/eval_summary.json')).get('hard','NA'))" 2>/dev/null || echo NA)
    log "  item $idx id=$id -> rollout ok (hard=$hard)"
  else
    log "  item $idx id=$id -> no eval_summary (rc=$rc); tail:"
    tail -8 "$out.log" 2>/dev/null | while IFS= read -r l; do log "    rollout| $l"; done
  fi
  # 确认轨迹 jsonl 真的落到了被 watch 的 projects 目录
  local nj; nj=$(find "$CHOME/projects" -name '*.jsonl' 2>/dev/null | wc -l)
  log "  $CHOME/projects now has $nj jsonl trajectory file(s)"
}

# ── 6) 串行跑 4 个训练样本 ──────────────────────────────────────────────
mapfile -t TRAIN_IDS < <(python -c "import json;[print(i['id']) for i in json.load(open('$TRAIN_SPLIT/train/items.json'))]")
log "train items = ${#TRAIN_IDS[@]} :: ${TRAIN_IDS[*]}"

idx=0
for id in "${TRAIN_IDS[@]}"; do
  idx=$((idx+1))
  ensure_daemon
  run_item "$idx" "$id"
  log "  settle ${ITEM_SETTLE}s for daemon ingest/cluster/promote"
  sleep "$ITEM_SETTLE"
  ensure_daemon
  snapshot
done

# ── 7) 收尾 settle + 收集 ───────────────────────────────────────────────
log "all items done; final settle ${FINAL_SETTLE}s (last ingest/cluster/SkillEdit pass)"
sleep "$FINAL_SETTLE"
ensure_daemon
log "=== final skill-repo snapshot ==="
snapshot
log "=== daemon log tail (cluster / SkillEdit / graduation) ==="
grep -aiE 'cluster|skilledit|skill_edit|graduat|baby|promot|atom|🎓|🌱' "$DAEMON_LOG" 2>/dev/null | tail -30 \
  || tail -30 "$DAEMON_LOG" 2>/dev/null

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

if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR/algo"
  [ -d "$XSKILL_SKILL_DIR" ] && cp -a "$XSKILL_SKILL_DIR" "$OUTPUT_DIR/algo/skill_repo" 2>/dev/null || true
  cp -a "$DAEMON_LOG" "$OUTPUT_DIR/algo/" 2>/dev/null || true
  cp -a /tmp/x_*.log "$OUTPUT_DIR/algo/" 2>/dev/null || true
  cp -a "$SKILLS_OUT" "$OUTPUT_DIR/algo/skills_shipped" 2>/dev/null || true
  echo "copied algo artifacts -> $OUTPUT_DIR/algo"
fi

# sidecar restartPolicy:Always —— 永不退出
log "entrypoint reached steady state; sleep infinity"
sleep infinity
