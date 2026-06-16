#!/usr/bin/env bash
# 打榜镜像 · skillopt（真训练 → 单 skill.md）。
# 离线自包含：构建上下文从仓库内 vendor/ 暂存，无任何机器私有路径。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"   # repo-relative: leadeboard_apps/spreadsheets_bench/<dir> -> repo root

# 暂存 Docker 构建上下文 _ctx（构建产物，已 gitignore，由本脚本从 vendor/ 重建）
rm -rf _ctx && mkdir _ctx
cp -r "$VENDOR/SkillOpt"   _ctx/SkillOpt
cp -r "$VENDOR/data_root"  _ctx/data_root

docker build -t "$REG/p_user1/algo-skillopt:v1" .
docker push "$REG/p_user1/algo-skillopt:v1"
echo "built+pushed $REG/p_user1/algo-skillopt:v1"
