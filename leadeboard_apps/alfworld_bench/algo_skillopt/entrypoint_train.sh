#!/usr/bin/env bash
# 打榜算法 · SkillOpt（ALFWorld）。镜像 SpreadsheetBench 版，改指 configs/alfworld +
# alfworld split。**本镜像代码齐备但默认不在榜单上提交运行**（只 noskill 跑通端到端）。
# 真训练需要 ALFWorld 数据（$ALFWORLD_DATA/json_2.1.1）与 TextWorld 运行栈。
#
# 真跑：SkillOpt train.py 在 alfworld env 上做 ReflACT 训练 -> best_skill.md ->
#       $SKILL_DIR/skill.md，写 ALGO + DONE，sleep infinity（sidecar 永不退出）。
set -uo pipefail
SKILL_OUT="${SKILL_DIR:-/shared/skill}"; mkdir -p "$SKILL_OUT"
KEY="${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY required}"
ENDPOINT="${TARGET_ENDPOINT:-https://api.deepseek.com}"
MODEL="${EVAL_MODEL:-deepseek-v4-flash}"
ALF="${ALFWORLD_DATA:-/alfworld_data}"
RUN=/tmp/run; rm -rf "$RUN"

export ALFWORLD_DATA="$ALF"
export ALFWORLD_WORKER_START_METHOD="${ALFWORLD_WORKER_START_METHOD:-spawn}"

# reduced scale（默认）：少量 train games，1 epoch。full：用全 train split。
if [ "${TRAIN_SCALE:-reduced}" = "full" ]; then SPLIT=/app/train_split; EOPT=""; TS=39; else SPLIT=/app/train_split; EOPT="train.num_epochs=1"; TS=6; fi

# 把相对 gamefile 展开为绝对（train.py 也直接拿 gamefile 当路径）。
python - "$SPLIT" "$ALF" <<'PY'
import json,os,sys
split,alf=sys.argv[1],sys.argv[2]
for sp in ("train","val","test"):
    p=os.path.join(split,sp,"items.json")
    if not os.path.isfile(p): continue
    items=json.load(open(p)); out=[]
    for it in items:
        r=dict(it); gf=str(r.get("gamefile") or "")
        if gf and not os.path.isabs(gf): r["gamefile"]=os.path.join(alf,gf)
        out.append(r)
    json.dump(out,open(p,"w"))
print("expanded gamefile paths under",split)
PY

cd /app/SkillOpt
python scripts/train.py --config configs/alfworld/default.yaml \
  --optimizer_backend openai_chat --target_backend openai_chat \
  --optimizer_model "$MODEL" --target_model "$MODEL" \
  --optimizer_azure_openai_endpoint "$ENDPOINT" --optimizer_azure_openai_api_key "$KEY" --optimizer_azure_openai_auth_mode openai_compatible \
  --target_azure_openai_endpoint "$ENDPOINT" --target_azure_openai_api_key "$KEY" --target_azure_openai_auth_mode openai_compatible \
  --split_dir "$SPLIT" --split_mode split_dir --seed 42 --workers "${WORKERS:-2}" --out_root "$RUN" \
  --cfg-options env.name=alfworld env.max_steps="${MAX_STEPS:-50}" train.train_size=$TS train.batch_size=3 \
                gradient.minibatch_size=3 gradient.merge_batch_size=3 $EOPT \
  || echo "WARN: train.py returned non-zero (will still try to ship best_skill.md)"

BEST=$(find "$RUN" -name best_skill.md | head -1)
if [ -s "$BEST" ]; then
  cp "$BEST" "$SKILL_OUT/skill.md"; echo skillopt > "$SKILL_OUT/ALGO"; touch "$SKILL_OUT/DONE"
  echo "DONE: $SKILL_OUT/skill.md"
else
  echo "FATAL: 无 best_skill.md — not shipping (sidecar must not exit)."
fi

if [ -n "${OUTPUT_DIR:-}" ]; then mkdir -p "$OUTPUT_DIR/algo" || true; cp -a "$RUN/." "$OUTPUT_DIR/algo/" 2>/dev/null || true; fi
sleep infinity
