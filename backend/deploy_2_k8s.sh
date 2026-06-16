#!/bin/bash

# --- 配置 ---
# set -e 会在任何命令失败时立即退出脚本
set -e

NAMESPACE="leaderboard"
DEPLOYMENT_NAME="leaderboard-api"
ADMIN_USER="${ADMIN_USER:-admin}"
# 不在仓库中硬编码口令。通过环境变量提供（默认 adminpass，与 init-db --create-defaults 一致）。
ADMIN_PASS="${ADMIN_PASS:-adminpass}"
# DB_PATH="/opt/leaderboard-db/leaderboard.db"
DB_PATH="/home/leaderboard-db/leaderboard.db"


echo "--- 步骤 1: 应用所有 K8s 资源配置 ---"
# 按顺序应用，确保 namespace 和 rbac 首先存在
kubectl apply -f namespace.yaml
kubectl apply -f rbac.yaml
kubectl apply -f backend-deployment.yaml
kubectl apply -f priorityclass.yaml
echo "K8s 资源已应用。"

echo "--- 步骤 2: 清理旧数据库 (在宿主机上) ---"
# -f (force) 确保即使文件不存在也不会报错
# rm -f $DB_PATH
echo "旧数据库文件 $DB_PATH 已删除。"

echo "--- 步骤 3: 强制 Deployment 滚动更新 (拉取新镜像) ---"
kubectl rollout restart deployment/$DEPLOYMENT_NAME -n $NAMESPACE
echo "Deployment 正在重启..."

echo "--- 步骤 4: 等待滚动更新完成 ---"
# 这将阻塞脚本，直到新 Pod 准备就绪 (Running & Ready)
kubectl rollout status deployment/$DEPLOYMENT_NAME -n $NAMESPACE
echo "Deployment 已成功更新！"

echo "--- 步骤 5: 获取新 Pod 的名称 ---"
# 因为 rollout 已经完成，这是获取新 Pod 名字的最可靠方法
sleep 5
POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=leaderboard-api -o jsonpath='{.items[0].metadata.name}')
echo "新 Pod 名称是: $POD_NAME"

echo "--- 步骤 6: 等待 Pod 内 Gunicorn 服务启动 (5秒) ---"
# 这是一个小的缓冲，确保 Pod 内的 app 完全启动后再执行 exec
sleep 5

echo "--- 步骤 7: 在新 Pod 中初始化数据库 ---"
kubectl exec -n $NAMESPACE $POD_NAME -- flask init-db
echo "数据库已初始化。"

echo "--- 步骤 8: 在新 Pod 中创建管理员账户 ---"
kubectl exec -n $NAMESPACE $POD_NAME -- flask create-admin $ADMIN_USER $ADMIN_PASS
echo "管理员 '$ADMIN_USER' 已创建。"

echo "--- 部署完成！---"

