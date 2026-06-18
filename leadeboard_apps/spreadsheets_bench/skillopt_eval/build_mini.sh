#!/usr/bin/env bash
# 榜单镜像 · mini 快速验收（5 题 test）。复用 single 模式 evaluator，只换 baked split。
# 临时把 bench_test_split 换成 ../data/test_mini5_split 构建，再 trap 还原（含失败路径）。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"; VENDOR="${VENDOR:-../../../vendor}"
[ -d _ctx/SkillOpt ] || { rm -rf _ctx && mkdir _ctx; cp -r "$VENDOR/SkillOpt" _ctx/SkillOpt; cp -r "$VENDOR/data_root" _ctx/data_root; }
rm -rf bench_test_split.bak && mv bench_test_split bench_test_split.bak
cp -r ../data/test_mini5_split bench_test_split
trap 'rm -rf bench_test_split && mv bench_test_split.bak bench_test_split' EXIT
docker build --build-arg TARGET_MODE=single -t "$REG/l_creator/spreadsheet-eval-single:mini5" .
docker push "$REG/l_creator/spreadsheet-eval-single:mini5"
echo "built+pushed spreadsheet-eval-single:mini5"
