#!/bin/bash
set -e

# ---------- 代理（可改/可空） ----------
export http_proxy="${http_proxy:-http://50.67.106.199:10086}"
export https_proxy="${https_proxy:-http://50.67.106.199:10086}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,*.local,leaderboard-api-svc}"

# ---------- 宿主机/镜像参数 ----------
HOST_APP_DIR="${HOST_APP_DIR:-$(pwd)}"
BASE_IMAGE="${BASE_IMAGE:-cr.rnd.huawei.com/ascend/vllm-ascend:v0.9.2rc1-310p-openeuler}"

# ---------- PIP 源 ----------
MIRROR_URL="${MIRROR_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
TRUSTED_HOST="${TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"

# ---------- 容器内路径 ----------
CONTAINER_APP_DIR="/app"
WHEEL_DIR_IN_CONTAINER="${CONTAINER_APP_DIR}/offline_wheels"
REQS_FILE_IN_CONTAINER="${CONTAINER_APP_DIR}/requirements.txt"
CONSTRAINT_FILE_IN_CONTAINER="/tmp/constraints.txt"
LOCK_FILE_IN_CONTAINER="/tmp/requirements.lock" # [新增] 锁文件路径

COMMAND_TO_RUN="
set -e
echo '--- 容器: Python 环境就绪 ---'

# 配置 pip 源
pip config set global.progress_bar off
pip config set global.index-url $MIRROR_URL
pip config set global.trusted-host $TRUSTED_HOST

# [新增] 配置 uv 源 (环境变量方式)
export UV_INDEX_URL=$MIRROR_URL

# 安装/更新 uv
echo '--- 安装 uv ---'
pip install -U uv

# 探测 torch 版本
PY_TORCH_VER=\$(python3 - <<'PY'
try:
    import torch
    print(torch.__version__)
except Exception:
    print('')
PY
)

UV_CONSTRAINT_ARGS=\"\"
if [ -n \"\$PY_TORCH_VER\" ]; then
  echo '--- 发现 torch 版本:' \"\$PY_TORCH_VER\"
  echo \"torch==\$PY_TORCH_VER\" > $CONSTRAINT_FILE_IN_CONTAINER
  UV_CONSTRAINT_ARGS=\"--constraint $CONSTRAINT_FILE_IN_CONTAINER\" # [修改] 传给 uv
else
  echo '--- 未发现 torch。将不使用约束。'
fi

# 准备 wheel 目录
rm -rf $WHEEL_DIR_IN_CONTAINER
mkdir -p $WHEEL_DIR_IN_CONTAINER

# [核心修改] 1. 使用 uv 解析依赖 (瞬间完成，解决回溯死循环)
echo '--- 正在使用 uv 解析依赖树 (生成 lock 文件)... ---'
uv pip compile $REQS_FILE_IN_CONTAINER \$UV_CONSTRAINT_ARGS -o $LOCK_FILE_IN_CONTAINER

# [核心修改] 2. 使用 pip 纯下载 (使用 --no-deps 极大加速，因为依赖已在 lock 文件中全解)
echo '--- 正在根据 lock 文件高速下载离线包... ---'
pip download --resume-retries 10 -d $WHEEL_DIR_IN_CONTAINER --no-deps -r $LOCK_FILE_IN_CONTAINER

# 保险：去掉目录里任何 torch/torch_npu wheel (如果 uv 解析出来了)
rm -f $WHEEL_DIR_IN_CONTAINER/torch-*.whl || true
rm -f $WHEEL_DIR_IN_CONTAINER/torch_npu-*.whl || true

echo '--- 完成。离线包在:' $WHEEL_DIR_IN_CONTAINER
"

echo "--- 准备启动临时容器... ---"
echo "--- 挂载 $HOST_APP_DIR 到 $CONTAINER_APP_DIR ---"

docker run --rm -it --network=host \
  -e http_proxy -e https_proxy -e no_proxy \
  -v "${HOST_APP_DIR}:${CONTAINER_APP_DIR}" \
  --workdir "${CONTAINER_APP_DIR}" \
  "${BASE_IMAGE}" \
  /bin/bash -c "${COMMAND_TO_RUN}"

echo "--- ✅ 离线包已生成/更新：${HOST_APP_DIR}/offline_wheels ---"

