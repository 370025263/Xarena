#!/usr/bin/env bash
# 榜单评测镜像 · ALFWorld（SkillOpt eval_only.py 跑 ALFWorld TextWorld ReAct rollout）。
# 离线自包含：构建上下文从仓库内 vendor/ 暂存 SkillOpt，数据用本目录 baked miniset。
#
# 用法:
#   bash build.sh                 # miniset 镜像（baked 3-game，默认），构建+推送
#   ALFWORLD_DATASET=none bash build.sh   # 不 bake 数据（数据运行期挂载/下载）
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"   # repo-relative -> repo root vendor/
DATASET="${ALFWORLD_DATASET:-miniset}"

# 暂存 Docker 构建上下文（构建产物，已 gitignore，由本脚本从 vendor/ 与 ../data 重建）。
# build context = 本目录；SkillOpt 来自 vendor/，data/ 来自 bench 根 ../data。
rm -rf _ctx && mkdir _ctx
cp -r "$VENDOR/SkillOpt" _ctx/SkillOpt
rm -rf data && cp -r ../data data

if [ "$DATASET" = "miniset" ]; then
  SPLIT_DIR_ARG=/bench/data/alfworld_miniset
else
  SPLIT_DIR_ARG=/bench/data/alfworld_path_split
fi

echo "--- building alfworld-eval (dataset=$DATASET) ---"
docker build \
  --build-arg ALFWORLD_DATASET="$DATASET" \
  --build-arg SPLIT_DIR="$SPLIT_DIR_ARG" \
  -t "$REG/l_creator/alfworld-eval:miniset" \
  -t "$REG/l_creator/alfworld-eval:latest" .
docker push "$REG/l_creator/alfworld-eval:miniset"
docker push "$REG/l_creator/alfworld-eval:latest"
echo "built+pushed alfworld-eval ($DATASET)"
