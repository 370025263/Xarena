#!/bin/bash
set -e
export http_proxy="${http_proxy:-}"; export https_proxy="${https_proxy:-}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,*.local,leaderboard-api-svc}"
HOST_APP_DIR="${HOST_APP_DIR:-$(pwd)}"
BASE_IMAGE="${BASE_IMAGE:-python:3.11-slim}"
MIRROR_URL="${MIRROR_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
TRUSTED_HOST="${TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
docker run --rm --network=host -e http_proxy -e https_proxy -e no_proxy \
  -v "${HOST_APP_DIR}:/app" --workdir /app "${BASE_IMAGE}" /bin/bash -c "
    set -e; pip config set global.index-url ${MIRROR_URL}; pip config set global.trusted-host ${TRUSTED_HOST}
    rm -rf /app/offline_wheels && mkdir -p /app/offline_wheels
    pip download -d /app/offline_wheels -r /app/requirements.txt"
echo \"OK: offline_wheels in \${HOST_APP_DIR}/offline_wheels\"
