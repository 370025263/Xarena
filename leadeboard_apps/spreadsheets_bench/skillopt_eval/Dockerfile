FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn CLAUDE_CODE_EXEC_USE_SDK=cli
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git bash && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code && claude --version || true
COPY requirements.eval.txt /app/requirements.eval.txt
RUN pip install --no-cache-dir -r /app/requirements.eval.txt
COPY _ctx/SkillOpt /app/SkillOpt
RUN pip install --no-cache-dir -e /app/SkillOpt || true
ENV PYTHONPATH=/app/SkillOpt
COPY _ctx/data_root /data
WORKDIR /app
COPY evaluator.py /app/evaluator.py
COPY bench_test_split/ /bench/data/test_10pct_split/
ARG TARGET_MODE=single
ENV TARGET_MODE=${TARGET_MODE} SKILLOPT_DIR=/app/SkillOpt EVAL_PY=/usr/local/bin/python DATA_ROOT=/data \
    SPLIT_DIR=/bench/data/test_10pct_split SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash WORKERS=3 \
    IS_SANDBOX=1
# IS_SANDBOX=1: the eval container runs as root in k8s; claude refuses
# --dangerously-skip-permissions as root unless IS_SANDBOX is set.
CMD ["python","/app/evaluator.py"]
