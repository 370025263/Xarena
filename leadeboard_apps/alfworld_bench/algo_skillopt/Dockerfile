FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
# ALFWorld training needs the TextWorld stack -> build toolchain for fast-downward-textworld.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git bash curl ca-certificates unzip build-essential cmake \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY _ctx/SkillOpt /app/SkillOpt
RUN pip install --no-cache-dir -e /app/SkillOpt || true
ENV PYTHONPATH=/app/SkillOpt
WORKDIR /app
# Train split (ALFWorld path manifest; gamefiles relative to $ALFWORLD_DATA).
COPY train_split/ /app/train_split/
# Optional baked miniset game data (so the image can train offline on a few games).
ARG ALFWORLD_DATASET=miniset
COPY data_stage/ /app/_data_stage/
RUN if [ "$ALFWORLD_DATASET" = "miniset" ]; then \
        mkdir -p /alfworld_data && cp -a /app/_data_stage/. /alfworld_data/ ; \
    fi && rm -rf /app/_data_stage
COPY entrypoint_train.sh /app/entrypoint_train.sh
RUN chmod +x /app/entrypoint_train.sh
ENV SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash WORKERS=2 MAX_STEPS=50 \
    ALFWORLD_DATA=/alfworld_data ALFWORLD_WORKER_START_METHOD=spawn TRAIN_SCALE=reduced
CMD ["bash","/app/entrypoint_train.sh"]
