#!/usr/bin/env bash
# 打榜镜像 · trace2skill（真训练 → 单 skill.md）。
# 离线自包含：构建上下文从仓库内 vendor/ 暂存，无任何机器私有路径。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"   # repo-relative -> repo root vendor/

rm -rf _ctx && mkdir -p _ctx
# vendor/Trace2Skill 已经是去掉 data/.git 的精简源码（见 vendor/README.md）
cp -r "$VENDOR/Trace2Skill" _ctx/Trace2Skill
# 26M SpreadsheetBench 数据作为 /data
cp -r "$VENDOR/data_root"   _ctx/data_root

docker build -t "$REG/p_user1/algo-trace2skill:v1" .
docker push "$REG/p_user1/algo-trace2skill:v1"
echo "built+pushed $REG/p_user1/algo-trace2skill:v1"
