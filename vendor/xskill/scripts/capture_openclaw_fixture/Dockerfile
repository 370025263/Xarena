FROM node:24-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates jq \
    && rm -rf /var/lib/apt/lists/*

# 装本机一致版本，避免 trajectory schema 漂移
RUN npm install -g openclaw@2026.5.7 \
    && rm -rf /root/.npm

WORKDIR /work
ENV HOME=/work

COPY openclaw.json /work/.openclaw/openclaw.json
COPY run.sh /work/run.sh
RUN chmod +x /work/run.sh

ENTRYPOINT ["/work/run.sh"]
