#!/usr/bin/env bash
# Build cache-friendly Docker images for the leaderboard and push to the local
# registry (localhost:5000). No internal proxy (direct/tuna egress). Tags are
# self-describing.
#   - leaderboard-api  : Flask backend (local-executor + sqlite build)
#   - leaderboard-ui   : Streamlit frontend (points at API via env)
# DockerHub is TLS-intercepted here, so the python base is pre-pulled from a
# mirror and tagged python:3.10-slim by the caller.
set -euo pipefail
REG="${REG:-localhost:5000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "######## backend image ########"
cd "$ROOT/backend"
# build-args left empty on purpose: direct egress (no unreachable internal proxy)
docker build \
  --build-arg http_proxy= --build-arg https_proxy= --build-arg no_proxy=localhost,127.0.0.1 \
  -t "$REG/leaderboard-api:v1-localexec-sqlite" \
  -t "$REG/leaderboard-api:latest" .
docker push "$REG/leaderboard-api:v1-localexec-sqlite"
docker push "$REG/leaderboard-api:latest"

echo "######## frontend image ########"
cd "$ROOT/frontend"
docker build \
  --build-arg http_proxy= --build-arg https_proxy= --build-arg no_proxy=localhost,127.0.0.1 \
  -t "$REG/leaderboard-ui:v1-dsv4flash" \
  -t "$REG/leaderboard-ui:latest" .
docker push "$REG/leaderboard-ui:v1-dsv4flash"
docker push "$REG/leaderboard-ui:latest"

echo "######## registry catalog ########"
curl -s "$REG/v2/_catalog"; echo
echo "DONE"
