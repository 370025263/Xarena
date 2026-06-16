FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
RUN apt-get update && apt-get install -y --no-install-recommends git bash && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY _ctx/SkillOpt /app/SkillOpt
RUN pip install --no-cache-dir -e /app/SkillOpt || true
ENV PYTHONPATH=/app/SkillOpt
COPY _ctx/data_root /data
WORKDIR /app
COPY train_split/ /app/train_split/
COPY entrypoint_train.sh /app/entrypoint_train.sh
RUN chmod +x /app/entrypoint_train.sh
ENV SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash WORKERS=4 TRAIN_SCALE=reduced
CMD ["bash","/app/entrypoint_train.sh"]
