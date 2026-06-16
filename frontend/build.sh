#!/bin/bash
set -eo pipefail

# --- 配置 ---
IMAGE_NAME="leaderboard-streamlit-ui" # Streamlit UI 的镜像名称
IMAGE_TAG="latest"
LOCAL_REGISTRY="localhost:5000"
# 推送到 Registry 的路径
PUSH_IMAGE_NAME="${LOCAL_REGISTRY}/ui/${IMAGE_NAME}:${IMAGE_TAG}"

# --- 颜色 ---
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- 代理设置 ---
export http_proxy="http://50.67.89.137:10086"
export https_proxy="http://50.67.89.137:10086"
# 添加本地 Registry 到 no_proxy，防止推送时走代理
export no_proxy="localhost,127.0.0.1,.local,${LOCAL_REGISTRY}"

echo -e "${BLUE}--- 1. 正在构建 Streamlit UI Docker 镜像 (使用代理: ${http_proxy}) ---${NC}"

# 使用 --progress=plain 打印详细日志
docker build \
  --progress=plain \
  --build-arg http_proxy=${http_proxy} \
  --build-arg https_proxy=${https_proxy} \
  --build-arg no_proxy=${no_proxy} \
  -t "${IMAGE_NAME}:${IMAGE_TAG}" .

echo -e "${GREEN}成功: 镜像构建完成。${NC}"

echo -e "${BLUE}--- 2. 正在标记镜像: ${PUSH_IMAGE_NAME} ---${NC}"
docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${PUSH_IMAGE_NAME}"
echo -e "${GREEN}成功: 镜像标记完成。${NC}"

echo -e "${BLUE}--- 3. 正在推送镜像到本地 Registry: ${PUSH_IMAGE_NAME} ---${NC}"
docker push "${PUSH_IMAGE_NAME}"
echo -e "${GREEN}成功: 镜像已推送到 ${LOCAL_REGISTRY}。${NC}"

echo -e "${GREEN}--- 构建脚本执行完毕 ---${NC}"

