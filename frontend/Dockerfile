# 使用 Python 3.10 slim 作为基础镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# --- (关键) 保留您之前的网络设置 ---
# 声明从 --build-arg 接收的代理变量
ARG http_proxy
ARG https_proxy
ARG no_proxy

# 将代理设置为环境变量，以便 pip 等工具可以使用
ENV http_proxy=${http_proxy}
ENV https_proxy=${https_proxy}
ENV no_proxy=${no_proxy}

# 告诉 Python 不要缓存 stdout/stderr
ENV PYTHONUNBUFFERED=1
# --- (网络设置结束) ---

# --- 安装依赖 ---
# 使用您之前的 pip 配置: 国内镜像, trusted-host, no progress bar
# 注意: 我们直接在这里使用 ENV 设置的代理
RUN pip config set global.progress_bar off && \
    pip install --no-cache-dir \
        --trusted-host pypi.tuna.tsinghua.edu.cn -i https://pypi.tuna.tsinghua.edu.cn/simple \
        -r requirements.txt

# 复制 Streamlit 应用代码
COPY app.py .

# 暴露 Streamlit 默认端口 8501
EXPOSE 8501

# 启动 Streamlit 应用
# --server.port 确保它监听在暴露的端口上
# --server.address 0.0.0.0 使其可以从容器外部访问
# --server.headless true 在无头环境中运行，禁用浏览器自动打开
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]

