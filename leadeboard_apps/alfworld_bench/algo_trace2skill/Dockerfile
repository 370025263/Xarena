FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
RUN apt-get update && apt-get install -y --no-install-recommends git bash && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY _ctx/Trace2Skill /app/Trace2Skill
RUN pip install --no-cache-dir -e /app/Trace2Skill || true
ENV PYTHONPATH=/app/Trace2Skill
WORKDIR /app
COPY seed_skill.md /app/seed_skill.md
COPY entrypoint_train.sh /app/entrypoint_train.sh
RUN chmod +x /app/entrypoint_train.sh
ENV SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash WORKERS=2 OPENAI_BASE_URL=https://api.deepseek.com
CMD ["bash","/app/entrypoint_train.sh"]
