#!/usr/bin/env bash
# 榜单镜像 · multi 模式。test split 共享同一份评测代码，模式由 build-arg 烘焙。
# 离线自包含：若 _ctx 未暂存则从仓库内 vendor/ 暂存（与 build.sh 一致）。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"
if [ ! -d _ctx/SkillOpt ] || [ ! -d _ctx/data_root ]; then
  rm -rf _ctx && mkdir _ctx
  cp -r "$VENDOR/SkillOpt"  _ctx/SkillOpt
  cp -r "$VENDOR/data_root" _ctx/data_root
fi
docker build --build-arg TARGET_MODE=multi \
  -t "$REG/l_creator/spreadsheet-eval-multi:dsv4flash-10pct" \
  -t "$REG/l_creator/spreadsheet-eval-multi:latest" .
docker push "$REG/l_creator/spreadsheet-eval-multi:dsv4flash-10pct"
docker push "$REG/l_creator/spreadsheet-eval-multi:latest"
echo "built+pushed spreadsheet-eval-multi"
