#!/usr/bin/env bash
# Canonical frontend launch: run the built leaderboard-ui image as a container
# on :7799 (dockerd-managed, survives the shell session). Lighter auto-refresh
# than the 5s default so the detail page (Plotly radar) stays responsive.
set -uo pipefail
docker rm -f leaderboard-ui 2>/dev/null || true
docker run -d --name leaderboard-ui --network host --restart always \
  -e API_BASE_URL=http://localhost:7789 \
  -e PUBLIC_BASE_URL=https://algo.xskill.wiki \
  -e LB_RANK_REFRESH_MS=30000 -e LB_LIST_REFRESH_MS=15000 \
  -e LB_QUEUE_REFRESH_MS=15000 -e LB_HOME_TOP5_REFRESH_MS=30000 \
  localhost:5000/leaderboard-ui:v1-dsv4flash \
  streamlit run app.py --server.port=7799 --server.address=0.0.0.0 \
    --server.headless=true --browser.gatherUsageStats=false \
    --server.enableCORS=false --server.enableXsrfProtection=false \
    --server.enableWebsocketCompression=false
echo "leaderboard-ui container started on :7799"
