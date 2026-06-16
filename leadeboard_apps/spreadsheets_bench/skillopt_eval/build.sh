#!/usr/bin/env bash
# 榜单评测镜像 · 构建全部三种模式 (single / multi / react)。
# 三种模式共享同一份 evaluator.py 与构建上下文，模式由 build-arg TARGET_MODE 烘焙。
# 离线自包含：构建上下文从仓库内 vendor/ 暂存，无任何机器私有路径。
#
# 用法:
#   bash build.sh            # 暂存 _ctx 并构建 single+multi+react，推送到 registry
#   bash build.sh single     # 只构建某一种模式
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"   # repo-relative -> repo root vendor/
MODES=("${@:-single multi react}"); MODES=(${MODES[@]})

# 暂存 Docker 构建上下文 _ctx（构建产物，已 gitignore，由本脚本从 vendor/ 重建）
rm -rf _ctx && mkdir _ctx
cp -r "$VENDOR/SkillOpt"  _ctx/SkillOpt
cp -r "$VENDOR/data_root" _ctx/data_root

for m in "${MODES[@]}"; do
  echo "--- building spreadsheet-eval-$m ---"
  docker build --build-arg TARGET_MODE="$m" \
    -t "$REG/l_creator/spreadsheet-eval-$m:dsv4flash-10pct" \
    -t "$REG/l_creator/spreadsheet-eval-$m:latest" .
  docker push "$REG/l_creator/spreadsheet-eval-$m:dsv4flash-10pct"
  docker push "$REG/l_creator/spreadsheet-eval-$m:latest"
  echo "built+pushed spreadsheet-eval-$m"
done
