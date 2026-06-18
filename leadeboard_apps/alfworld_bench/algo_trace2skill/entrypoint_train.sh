#!/usr/bin/env bash
# 打榜算法 · Trace2Skill（ALFWorld）。**代码齐备，默认不在榜单上提交运行**。
#
# 诚实说明（重要）:
#   Trace2Skill 的轨迹蒸馏管线是 *SpreadsheetBench 专用* 的（run_spreadsheetbench.py、
#   spreadsheet_agent/、evaluate_with_official.py 全部围绕 xlsx）。vendor/Trace2Skill 里
#   没有任何 ALFWorld 适配（grep alfworld == 0 命中）。把它真正迁到 ALFWorld 需要新写一套
#   ALFWorld 轨迹采集 + 评测管线，超出「代码齐备、默认不运行」的范围，且无法在不实跑的情况下伪造。
#
#   因此本镜像走 Trace2Skill 的 *通用 skill 进化* 子系统（skill_evolver），以 ALFWorld 的
#   initial skill 为种子做一次进化占位，产出 $SKILL_DIR/skill.md。这条路径需要轨迹/错误分析
#   输入才有意义；缺 ALFWorld 轨迹时它退化为「原样交付种子 skill」。
#   真正的 ALFWorld Trace2Skill 需补齐 run_alfworld.py 等（见 README）。
set -uo pipefail
SKILL_OUT="${SKILL_DIR:-/shared/skill}"; mkdir -p "$SKILL_OUT"
MODEL="${EVAL_MODEL:-deepseek-v4-flash}"
SEED_SKILL="${SEED_SKILL:-/app/seed_skill.md}"
RUN=/tmp/run; rm -rf "$RUN"; mkdir -p "$RUN"

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.deepseek.com}"
export OPENAI_API_KEY="${DEEPSEEK_API_KEY:-}"

cd /app/Trace2Skill || { echo "FATAL: Trace2Skill 源码缺失"; sleep infinity; }

# 占位：若有 ALFWorld 错误分析 JSON 则进化，否则原样交付种子 skill。
ERR_JSON="${ALFWORLD_ERROR_ANALYSIS:-}"
if [ -n "$ERR_JSON" ] && [ -s "$ERR_JSON" ]; then
  EVOLVED="$RUN/skills/alfworld"; mkdir -p "$EVOLVED"; cp "$SEED_SKILL" "$EVOLVED/SKILL.md"
  python -m skill_evolver.run_parallel_skill_evolution --input-json "$ERR_JSON" \
    --skill-dir "$EVOLVED" --model "$MODEL" --verbose --batch-size 1 \
    --changelog "$RUN/change.log" --max-workers "${WORKERS:-2}" --prompt generic \
    --patch-pipeline json --seed 42 || echo "WARN: evolution non-zero (ship seed)"
  SRC="$EVOLVED/SKILL.md"
else
  echo "NOTE: no ALFWorld error-analysis input; shipping seed skill unchanged."
  SRC="$SEED_SKILL"
fi

if [ -s "$SRC" ]; then
  cp "$SRC" "$SKILL_OUT/skill.md"; echo trace2skill > "$SKILL_OUT/ALGO"; touch "$SKILL_OUT/DONE"
  echo "DONE: $SKILL_OUT/skill.md"
else
  echo "FATAL: no skill to ship (sidecar must not exit)."
fi

if [ -n "${OUTPUT_DIR:-}" ]; then mkdir -p "$OUTPUT_DIR/algo" || true; cp -a "$RUN/." "$OUTPUT_DIR/algo/" 2>/dev/null || true; fi
sleep infinity
