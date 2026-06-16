#!/usr/bin/env bash
# ============================================================================
# Backend local-executor entrypoint for the spreadsheet skill leaderboard.
# Runs the single-mode (with-harness) eval for the submitted skill, then posts
# the score + per-task details back to the backend.
#
# Invoked by the backend with env:
#   SUBMISSION_ID      (required)
#   API_INTERNAL_URL   backend base url (default http://localhost:7789)
#   SKILL_ALGO         skillopt | trace2skill | xskill | noskill (default noskill)
#   WORKERS            eval concurrency (default 3)
# Output dir defaults under EVAL_RUNS_DIR/<submission_id>.
# ============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# repo root: skillopt_eval -> spreadsheets_bench -> leadeboard_apps -> repo
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"

SUBMISSION_ID="${SUBMISSION_ID:?SUBMISSION_ID required}"
export API_INTERNAL_URL="${API_INTERNAL_URL:-http://localhost:7789}"
export SKILL_ALGO="${SKILL_ALGO:-noskill}"
export WORKERS="${WORKERS:-3}"
export EVAL_MODE="single"
export EVAL_MODEL="${EVAL_MODEL:-deepseek-v4-flash}"

EVAL_RUNS_DIR="${EVAL_RUNS_DIR:-$REPO_ROOT/data/eval_runs}"
export OUT_DIR="$EVAL_RUNS_DIR/sub_${SUBMISSION_ID}_${SKILL_ALGO}"
BENCH_DIR="${BENCH_DIR:-$REPO_ROOT/leadeboard_apps/spreadsheets_bench}"
export SPLIT_DIR="${SPLIT_DIR:-$BENCH_DIR/data/test_10pct_split}"
# Fresh output dir every run: prevents resume from a stale results.jsonl when a
# submission id is reused (e.g. after a deletion). The eval has resume-on-existing
# semantics keyed by out_dir, so a clean dir guarantees a full re-run.
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

echo "############ submission $SUBMISSION_ID  algo=$SKILL_ALGO ############"
bash "$HERE/run_single_eval.sh" "$SKILL_ALGO" "$OUT_DIR"
rc=$?
echo "############ eval rc=$rc -> posting results ############"
"${EVAL_PY:-python3}" "$HERE/post_results.py"
prc=$?
echo "############ post rc=$prc ############"
exit $(( rc != 0 ? rc : prc ))
