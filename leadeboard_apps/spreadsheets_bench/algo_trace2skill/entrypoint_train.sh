#!/usr/bin/env bash
# Trace2Skill 打榜训练入口（reduced scale, 6 tasks, max_turns 30）。
# 真跑 6 步管线: 1产轨迹 -> 2评测 -> 3analyze -> 4错误分析(agentic) -> 5解析 -> 6进化。
# 产出 evolution/skills/xlsx/SKILL.md，复制到 $SKILL_DIR/skill.md，写 ALGO + DONE，然后 sleep infinity。
# sidecar restartPolicy:Always —— 任何情况下都不能退出（退出会无限重训），故失败也 sleep infinity。
set -uo pipefail

SKILL_OUT="${SKILL_DIR:-/shared/skill}"; mkdir -p "$SKILL_OUT"
KEY="${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY required}"
BASE="${OPENAI_BASE_URL:-https://api.deepseek.com}"
MODEL="${EVAL_MODEL:-deepseek-v4-flash}"
W="${WORKERS:-4}"
MT="${MAX_TURNS:-30}"

export OPENAI_BASE_URL="$BASE"
export OPENAI_API_KEY="$KEY"

T2S=/app/Trace2Skill
DATA=/app/t2s_train
RUN=/tmp/run; rm -rf "$RUN"; mkdir -p "$RUN"
GEN=/tmp/gen.json
echo '{"temperature": 1.0, "top_p": 1.0, "timeout": 600}' > "$GEN"

cd "$T2S"

echo "### 1/6 产轨迹 (reduced, max_turns=$MT, workers=$W) ###"
python run_spreadsheetbench.py --data_path "$DATA" --model "$MODEL" --agent cli_skill_preloaded \
  --skills_dir spreadsheet_agent/skills --log_dir "$RUN/logs" --log_format markdown \
  --working_dir "$RUN/work" --output_dir "$RUN/outputs" --max_turns "$MT" --workers "$W" \
  --generation_config "$GEN" --seeds 42 || echo "WARN: step1 returned non-zero (continuing)"

echo "### 2/6 评测 (non-fatal) ###"
python evaluate_with_official.py --data_path "$DATA" --output_dir "$RUN/outputs" --verbose || echo "WARN: step2 evaluate failed (non-fatal)"

echo "### 3/6 analyze_results (non-fatal) ###"
python analyze_results.py --eval_results "$RUN/outputs/eval_official_results.json" --log_dir "$RUN/logs" || echo "WARN: step3 analyze failed (non-fatal)"

echo "### 4/6 错误分析 (agentic, max_turns=$MT) ###"
python analysis/run_error_analysis.py --data_path "$DATA" --work_dir "$RUN/work" --logs_dir "$RUN/logs" \
  --output_dir "$RUN/error_analysis" --model "$MODEL" --workers "$W" --generation_config "$GEN" --max_turns "$MT" \
  || echo "WARN: step4 error_analysis returned non-zero (continuing)"

echo "### 5/6 解析错误记录 ###"
python analysis/parse_error_analysis_outputs.py --input_dir "$RUN/error_analysis" --output "$RUN/error_analysis_parsed.json" \
  || echo "WARN: step5 parse returned non-zero (continuing)"

echo "### 6/6 进化 (error-driven, 深化 xlsx) ###"
EVOLVED="$RUN/evolution/skills"; mkdir -p "$EVOLVED"; cp -r spreadsheet_agent/skills/. "$EVOLVED"
python -m skill_evolver.run_parallel_skill_evolution --input-json "$RUN/error_analysis_parsed.json" \
  --skill-dir "$EVOLVED/xlsx" --model "$MODEL" --verbose --batch-size 1 --changelog "$RUN/evolution/change.log" \
  --max-workers "$W" --prompt generic --generation-config "$GEN" --patch-pipeline json --seed 42 \
  || echo "WARN: step6 evolution returned non-zero (will still try to ship SKILL.md)"

SKILL_SRC="$EVOLVED/xlsx/SKILL.md"
if [ -s "$SKILL_SRC" ]; then
  cp "$SKILL_SRC" "$SKILL_OUT/skill.md"
  echo trace2skill > "$SKILL_OUT/ALGO"
  touch "$SKILL_OUT/DONE"
  echo "DONE: shipped $SKILL_OUT/skill.md ($(wc -c < "$SKILL_OUT/skill.md") bytes)"
else
  echo "FATAL: no evolved SKILL.md at $SKILL_SRC — not shipping. Sleeping (sidecar must not exit)."
fi

# sidecar restartPolicy:Always —— 永不退出
sleep infinity
