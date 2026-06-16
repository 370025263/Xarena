
# 1. 使用官方 Python 3.10 slim 版本作为基础镜像
FROM python:3.10-slim

# 2. 设置工作目录
WORKDIR /app

# 3. 复制依赖文件
COPY requirements.txt .

ARG http_proxy
ARG https_proxy
ARG no_proxy

# 4. 安装依赖
# 我们使用 --no-cache-dir 来减小镜像体积

RUN pip config set global.progress_bar off
RUN env http_proxy=$http_proxy \
        https_proxy=$https_proxy \
        no_proxy=$no_proxy \
    pip config set global.progress_bar off && \
    pip install --no-cache-dir \
        --trusted-host pypi.tuna.tsinghua.edu.cn -i https://pypi.tuna.tsinghua.edu.cn/simple \
        -r requirements.txt
# 5. 复制应用代码
COPY app.py .
COPY log_collector.py  .
COPY agent/ agent/
# 6. 暴露端口
# Gunicorn 默认绑定到 8000 端口 (我们将在 CMD 中指定)
EXPOSE 8000


# 7. 运行应用的命令
# 使用 Gunicorn 作为生产级的 WSGI 服务器
# app:app 指的是 app.py 文件中的 app 实例
# CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "app:app"]
# 使用 gevent 模式，适合处理 Streamlit 这种频繁的 IO 请求
CMD ["gunicorn", "--workers", "4", "--worker-class", "gevent", "--bind", "0.0.0.0:8000", "app:app"]

