#!/usr/bin/env bash
# 打榜算法 · noskill（SpreadsheetBench baseline）。
# 不训练：直接交付一个空/no-op skill 到共享卷，写 ALGO + DONE，然后 sleep infinity。
# 用途：以最快的 train（瞬时）打通端到端管线，验证评测/回传/结果视图（远快于
# skillopt ~24min 真训练）。
#
# 约定（与 skillopt/trace2skill 一致，single skill.md）:
#   - SKILL_DIR=/shared/skill；写单个 skill.md（single 约定）+ ALGO + touch DONE。
#   - 空 skill 内容 -> evaluator 以 --skill <empty.md> 注入，即不向 harness 注入任何技能。
#   - sidecar restartPolicy:Always —— 写完后必须 sleep infinity，绝不退出（退出会重跑）。
set -uo pipefail

SKILL_OUT="${SKILL_DIR:-/shared/skill}"
mkdir -p "$SKILL_OUT"

# no-op skill：空内容（仅一行注释便于人看）。
cat > "$SKILL_OUT/skill.md" <<'EOF'
<!-- noskill baseline: no skill injected; the agent solves SpreadsheetBench with no extra guidance. -->
EOF

echo noskill > "$SKILL_OUT/ALGO"
touch "$SKILL_OUT/DONE"
echo "DONE: shipped no-op $SKILL_OUT/skill.md (noskill baseline)"

# 通用产物落盘：把交付的 skill 拷到 $OUTPUT_DIR/algo（与 eval 的 $OUTPUT_DIR/eval 对称）。
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR/algo" || true
  cp -a "$SKILL_OUT/." "$OUTPUT_DIR/algo/" 2>/dev/null || true
  echo "copied algo artifacts -> $OUTPUT_DIR/algo"
fi

# sidecar restartPolicy:Always —— 永不退出
sleep infinity
