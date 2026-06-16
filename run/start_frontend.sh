#!/usr/bin/env bash
# Launch the Streamlit frontend on the host, pointing at the local backend.
# Auxiliary local-dev launcher (canonical entrypoint is repo-root ./install.sh).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"

# server-side API calls go to the local backend; browser-side (log streaming JS)
# uses PUBLIC_BASE_URL so a remote browser can reach it too (set to your host).
export API_BASE_URL="${API_BASE_URL:-http://localhost:7789}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:7789}"

# Lighter auto-refresh cadence than the 5s default: the detail page renders a
# Plotly radar + ranking tables, and 5s refresh kept it perpetually busy
# (sluggish for remote viewers, and never stable for headless capture).
export LB_QUEUE_REFRESH_MS="${LB_QUEUE_REFRESH_MS:-15000}"
export LB_RANK_REFRESH_MS="${LB_RANK_REFRESH_MS:-30000}"
export LB_LIST_REFRESH_MS="${LB_LIST_REFRESH_MS:-15000}"
export LB_HOME_TOP5_REFRESH_MS="${LB_HOME_TOP5_REFRESH_MS:-30000}"

exec "$ROOT/.venv-frontend/bin/streamlit" run app.py \
  --server.port=7799 --server.address=0.0.0.0 --server.headless=true \
  --browser.gatherUsageStats=false \
  --server.enableCORS=false --server.enableXsrfProtection=false
