#!/usr/bin/env bash
# Launch the leaderboard backend on the host (no K8s; local-executor mode).
# Auxiliary local-dev launcher. The canonical from-scratch entrypoint is the
# repo-root ./install.sh (K8s/kind). Paths are repo-relative (no host paths).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

export DATABASE_URL="${DATABASE_URL:-sqlite:///$ROOT/data/leaderboard.db}"
export UPLOADS_HOST_PATH="${UPLOADS_HOST_PATH:-$ROOT/data/uploads}"
export FLASK_RUN_PORT=7789
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-leaderboard-local-$(hostname)-secret}"
export JWT_EXPIRATION_HOURS=24

# local (no-k8s) execution: run evaluator as a host subprocess that posts back
export LOCAL_EXECUTOR=1
export API_INTERNAL_URL="http://localhost:7789"
export LOCAL_EXEC_CMD="$ROOT/leadeboard_apps/spreadsheets_bench/skillopt_eval/run_submission.sh"
export EVAL_RUNS_DIR="$ROOT/data/eval_runs"

# Optional keys for the agent-LLM feature. Prefer config.local.env (written by
# install.sh; gitignored); never hardcode keys here.
[ -f "$ROOT/config.local.env" ] && set -a && . "$ROOT/config.local.env" && set +a
export AGENT_LLM_BASE_URL="${AGENT_LLM_BASE_URL:-https://api.deepseek.com}"
export AGENT_LLM_MODEL="${AGENT_LLM_MODEL:-deepseek-chat}"
export AGENT_LLM_API_KEY="${AGENT_LLM_API_KEY:-${DEEPSEEK_API_KEY:-}}"

VENV="$ROOT/.venv-backend/bin"
exec "$VENV/gunicorn" --workers 1 --threads 8 --worker-class gthread \
  --timeout 120 --graceful-timeout 30 \
  --bind 0.0.0.0:7789 --access-logfile - --error-logfile - app:app
