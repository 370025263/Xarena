FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# Build toolchain for fast-downward-textworld (sdist; needs cmake + g++), plus
# unzip/curl for the (optional) baked ALFWorld miniset.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git bash curl ca-certificates unzip build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.eval.txt /app/requirements.eval.txt
RUN pip install --no-cache-dir -r /app/requirements.eval.txt

# SkillOpt sources (staged from repo vendor/ by build.sh)
COPY _ctx/SkillOpt /app/SkillOpt
RUN pip install --no-cache-dir -e /app/SkillOpt || true
ENV PYTHONPATH=/app/SkillOpt

WORKDIR /app
COPY evaluator.py /app/evaluator.py
COPY result_view.html /app/result_view.html

# Split manifest (gamefile paths relative to $ALFWORLD_DATA).
COPY data/alfworld_path_split/ /bench/data/alfworld_path_split/

# ALFWorld game data + the split the evaluator should eval.
#   ALFWORLD_DATASET=miniset  -> bake the committed 3-game miniset (default; offline, fast).
#                                Data -> /alfworld_data, split -> /bench/data/alfworld_miniset.
#   ALFWORLD_DATASET=none     -> no data baked; supply $ALFWORLD_DATA at runtime (mounted/fetched)
#                                and use the full split at /bench/data/alfworld_path_split.
ARG ALFWORLD_DATASET=miniset
COPY data/alfworld_miniset/ /bench/data/alfworld_miniset/
RUN if [ "$ALFWORLD_DATASET" = "miniset" ]; then \
        mkdir -p /alfworld_data && \
        cp -a /bench/data/alfworld_miniset/data/. /alfworld_data/ && \
        echo "baked miniset game data into /alfworld_data" ; \
    else echo "no data baked (ALFWORLD_DATASET=$ALFWORLD_DATASET)"; fi

# SPLIT_DIR: miniset build -> the 3-game test split; full build -> the 191-item split.
ARG SPLIT_DIR=/bench/data/alfworld_miniset
ENV SPLIT_DIR=${SPLIT_DIR}

ENV SKILLOPT_DIR=/app/SkillOpt EVAL_PY=/usr/local/bin/python \
    ALFWORLD_DATA=/alfworld_data \
    SKILL_DIR=/shared/skill EVAL_MODEL=deepseek-v4-flash EVAL_SPLIT=test \
    WORKERS=2 MAX_STEPS=50 \
    ALFWORLD_WORKER_START_METHOD=spawn \
    IS_SANDBOX=1
# IS_SANDBOX=1: the eval container runs as root in k8s (parity with the
# SpreadsheetBench eval image; harmless for the text engine).

CMD ["python","/app/evaluator.py"]
