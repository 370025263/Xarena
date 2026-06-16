#!/usr/bin/env bash
# 打榜镜像 · xskill（真训练 → 多 skill 文件夹，native Skill 工具）。
# 离线自包含：构建上下文从仓库内 vendor/ 暂存，无任何机器私有路径。
set -euo pipefail
cd "$(dirname "$0")"
REG="${REG:-localhost:5000}"
VENDOR="${VENDOR:-../../../vendor}"   # repo-relative -> repo root vendor/

rm -rf _ctx && mkdir -p _ctx

# xskill 源码（无 .git -> Dockerfile 用 SETUPTOOLS_SCM_PRETEND_VERSION 兜底）。
cp -r "$VENDOR/xskill"    _ctx/xskill
# SkillOpt（rollout 用 eval_only.py）。
cp -r "$VENDOR/SkillOpt"  _ctx/SkillOpt
# 26M SpreadsheetBench 数据作为 /data。
cp -r "$VENDOR/data_root" _ctx/data_root
# sync_skills_to.sh（参考脚本；entrypoint 实际用自带 collect_skills）。
cp "$VENDOR/sync_skills_to.sh" _ctx/sync_skills_to.sh

docker build -t "$REG/p_user1/algo-xskill:v1" .
docker push "$REG/p_user1/algo-xskill:v1"
echo "built+pushed $REG/p_user1/algo-xskill:v1"
