#!/usr/bin/env bash
set -uo pipefail
SKILL_OUT="${SKILL_DIR:-/shared/skill}"; mkdir -p "$SKILL_OUT"
KEY="${DEEPSEEK_API_KEY:?}"; ENDPOINT="${TARGET_ENDPOINT:-https://api.deepseek.com}"; MODEL="${EVAL_MODEL:-deepseek-v4-flash}"
RUN=/tmp/run; rm -rf "$RUN"
if [ "${TRAIN_SCALE:-reduced}" = "full" ]; then SPLIT=/app/full_split; EOPT=""; TS=80; else SPLIT=/app/train_split; EOPT="train.num_epochs=${SKILLOPT_EPOCHS:-4}"; TS="${SKILLOPT_TRAIN_SIZE:-20}"; fi
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
echo "DONE: $SKILL_OUT/skill.md"
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR/algo"
  cp -a "$RUN/." "$OUTPUT_DIR/algo/" 2>/dev/null || true
  echo "copied algo artifacts -> $OUTPUT_DIR/algo"
fi
sleep infinity
