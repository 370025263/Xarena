
# 使用我们刚刚加载的基础镜像
FROM cr.rnd.huawei.com/ascend/vllm-ascend:v0.9.2rc1-310p-openeuler

WORKDIR /app
COPY requirements.txt .

ARG http_proxy
ARG https_proxy
ARG no_proxy

# 4. 安装依赖
# 我们使用 --no-cache-dir 来减小镜像体积
# 告诉 Python 不要缓存 stdout/stderr
ENV PYTHONUNBUFFERED=1
# 关闭 dynamo / compile，强制 float16（vLLM 侧在 entrypoint.sh 指定）
ENV TORCH_COMPILE_DISABLE=1 \
    VLLM_COMPILATION_LEVEL=0 \
    VLLM_USE_DYNAMO=0 \
    TIKTOKEN_CACHE_DIR=/models/tiktoken/encoders/


# 你可以把自己修改的gitrepo替换到下面
# COPY deep-searcher-master ./deep-searcher-master/
COPY lightrag_hku-1.4.7-py3-none-any.whl .

# -------------------- [ 改动开始 ] --------------------

# [新] 1. 复制离线包到镜像内
COPY offline_wheels/ /app/offline_wheels/

# [改] 2. 修改 RUN 命令以使用离线包
#    - 删除了 env http_proxy... (因为 --no-index 不需要代理)
#    - 替换了 pip install uv (使用离线包)
#    - 替换了 uv pip install (使用 --no-index 和 --find-links)
#    - 在末尾添加了 rm -rf 清理离线包
RUN pip config set global.progress_bar off && \
    echo "--- 1/2: [Offline] 正在从离线包安装 uv ---" && \
    pip install \
        --no-index \
        --find-links=/app/offline_wheels/ \
        uv && \
    \
    echo "--- 2/2: [Offline] 正在离线安装 requirements.txt ---" && \
    uv pip install --system --no-cache-dir \
        --no-index \
        --find-links /app/offline_wheels/ \
        -r ./requirements.txt  && \
    \
    echo "--- [清理] 删除离线包以减小镜像体积 ---" && \
    rm -rf /app/offline_wheels

# -------------------- [ 改动结束 ] --------------------

ENV PYTHONUNBUFFERED=1
ENV http_proxy=$http_proxy
ENV https_proxy=$https_proxy
ENV no_proxy=$no_proxy

# 这里可能会经常改动 所以放在最后面
COPY app.py .
COPY rag.py .
COPY host_qwen3_embed.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
# COPY data ./data/


# 评测容器将通过 Pod 内部网络访问这个 5000 端口
EXPOSE 5000 8888 8889

# [新] 容器的启动命令改为 entrypoint 脚本
CMD ["./entrypoint.sh"]
