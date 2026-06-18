#!/usr/bin/env bash
# 打榜镜像 · Trace2Skill（ALFWorld）。**代码齐备，默认不在榜单上提交运行**（见 entrypoint 说明）。
# 离线自包含：Trace2Skill 从仓库 vendor/ 暂存。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"

rm -rf _ctx && mkdir _ctx
cp -r "$VENDOR/Trace2Skill" _ctx/Trace2Skill

docker build -t "$REG/p_user1/algo-alfworld-trace2skill:v1" .
docker push "$REG/p_user1/algo-alfworld-trace2skill:v1"
echo "built+pushed $REG/p_user1/algo-alfworld-trace2skill:v1 (code present; NOT submitted by default)"
