#!/usr/bin/env bash
# 打榜镜像 · noskill（SpreadsheetBench baseline；不训练，交付 no-op skill）。
# 自包含、无机器私有路径、无 vendor 依赖。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"

docker build -t "$REG/p_user1/spreadsheet-noskill:v1" .
docker push "$REG/p_user1/spreadsheet-noskill:v1"
echo "built+pushed $REG/p_user1/spreadsheet-noskill:v1"
