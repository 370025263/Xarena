#!/usr/bin/env bash
# 打榜镜像 · SkillOpt（ALFWorld）。**代码齐备，默认不在榜单上提交运行**（只 noskill 跑通）。
# 离线自包含：SkillOpt 从仓库 vendor/ 暂存；train split + miniset 从 bench data/ 暂存。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"

rm -rf _ctx && mkdir _ctx
cp -r "$VENDOR/SkillOpt" _ctx/SkillOpt

# train split：复用 bench 根 data/alfworld_path_split/train（+ val/test 供 split_dir 校验）。
rm -rf train_split && mkdir -p train_split
cp -r ../data/alfworld_path_split/train train_split/train
cp -r ../data/alfworld_path_split/val   train_split/val
cp -r ../data/alfworld_path_split/test  train_split/test

# baked miniset game data（让镜像可离线在几局上训练，非必需）。
rm -rf data_stage && cp -r ../data/alfworld_miniset/data data_stage

docker build -t "$REG/p_user1/algo-alfworld-skillopt:v1" .
docker push "$REG/p_user1/algo-alfworld-skillopt:v1"
echo "built+pushed $REG/p_user1/algo-alfworld-skillopt:v1 (code present; NOT submitted by default)"
