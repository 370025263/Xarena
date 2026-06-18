#!/usr/bin/env bash
# 测试用评测镜像 spreadsheet-eval-single-test:latest。
# 在 single 基础镜像之上叠加 result_view.html 插件（仅本测试镜像携带插件）。
#
# 用法:
#   bash build_test.sh             # BASE 默认 mini5（5 题，验收快）
#   BASE=<img> bash build_test.sh  # 指定其它基础镜像（如 :latest，28 题）
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
BASE="${BASE:-$REG/l_creator/spreadsheet-eval-single:mini5}"
IMG="$REG/l_creator/spreadsheet-eval-single-test:latest"

echo "--- building $IMG (BASE=$BASE) ---"
docker build --build-arg BASE="$BASE" -f Dockerfile.test -t "$IMG" .
docker push "$IMG"
kind load docker-image "$IMG" --name lb
echo "built+pushed+loaded $IMG"
