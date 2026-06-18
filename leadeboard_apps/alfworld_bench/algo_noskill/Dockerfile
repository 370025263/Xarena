FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
# noskill 不需要任何训练栈，只写文件 + sleep；保持镜像极小。
RUN apt-get update && apt-get install -y --no-install-recommends bash && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY entrypoint_train.sh /app/entrypoint_train.sh
RUN chmod +x /app/entrypoint_train.sh
ENV SKILL_DIR=/shared/skill
CMD ["bash","/app/entrypoint_train.sh"]
